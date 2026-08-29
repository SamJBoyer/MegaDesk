"""Consume CLOUDORDER signals, launch cloud agents, hand a PR off to GitHub.

Two loops, the same two MachineFactory runs, because the two halves have very
different clocks. Orders arrive in bursts and launch in under a second; the runs
they start take minutes, and the BE may be restarted while they are still going.
So the registry on the persistent DB is the source of truth rather than anything held in
memory:

* ``poll_orders`` reads the CLOUDORDER pub/sub channel, XADDs the stream as a
  reference, launches, and writes ``CLOUDRUN:<agent_id>`` before it does
  anything else it could fail at. Leftover stream entries do not start work.
* ``poll_runs`` walks that registry, asks Cursor where each run got to, and
  either hands off (first PR URL: store it, cancel the VM, no stream entry) or
  publishes CLOUDFINISHED for a real failure. Success for a cloud order lives
  on GitHub — merge-check files ``MERGE_SUCCESS`` / ``MERGE_FAIL`` — not on
  ``CLOUDFINISHED``.

The order of the first loop is the mirror image of the machine factory's. Cursor
mints the agent id, so the registry entry can only be written after the launch;
a local sandbox reads its own registry entry to find its work, so there the entry
has to exist first.

A launch that never happened and a run that failed are kept apart the whole way
through: ``startup_error`` may be retried, ``error`` may not, because a blind
retry of a launch that actually succeeded would open a second pull request.
"""

from __future__ import annotations

import logging
import os
import socket
import time
from typing import Any, Optional

from megadesk_contracts import (
    resolve_ephemeral_db,
    resolve_persistent_db,
    redis_connect,
    AgentError,
    AgentStartupError,
    resolve_redis_url,
)
from megadesk_contracts.wire import cloud as wire
from megadesk_contracts.wire.signal import FieldInbox

log = logging.getLogger("cloud_factory.manager")

POLL_INTERVAL_SEC = 1.0
# Cloud runs take minutes. Asking every second would spend the whole rate limit
# on questions rather than work.
RUN_POLL_INTERVAL_SEC = 10.0
RECONNECT_WAIT_SEC = 3.0
MAX_LAUNCH_ATTEMPTS = 3


class CloudFactoryManager:
    """Turn orders into cloud agents, and cloud agents into PR links."""

    def __init__(
        self,
        *,
        redis_url: Optional[str] = None,
        ephemeral: Any = None,
        persistent: Any = None,
        runtime: Any = None,
        group: str = wire.CLOUDORDER_GROUP,
        consumer: Optional[str] = None,
        poll_interval: float = POLL_INTERVAL_SEC,
        run_poll_interval: float = RUN_POLL_INTERVAL_SEC,
        retry_delay: float = RECONNECT_WAIT_SEC,
    ) -> None:
        self.redis_url = resolve_redis_url(redis_url)
        self.group = group
        self.consumer = consumer or f"{socket.gethostname()}-{os.getpid()}"
        self.poll_interval = float(poll_interval)
        self.run_poll_interval = float(run_poll_interval)
        self.retry_delay = float(retry_delay)
        self._ephemeral = ephemeral
        self._persistent = persistent
        self._runtime = runtime
        self._inbox: FieldInbox | None = None
        self._pending: list[tuple[str, dict[str, Any]]] = []
        self._next_run_poll = 0.0
        self._blocked_until: dict[str, float] = {}
        self._attempts: dict[str, int] = {}

    # --- Redis ---

    @property
    def ephemeral(self) -> Any:
        if self._ephemeral is None:
            self._ephemeral = redis_connect(
                self.redis_url, db=resolve_ephemeral_db(self.redis_url)
            )
        return self._ephemeral

    @property
    def persistent(self) -> Any:
        """Runs live on the persistent DB: they outlive both this process and the stream."""
        if self._persistent is None:
            self._persistent = redis_connect(
                self.redis_url, db=resolve_persistent_db(self.redis_url)
            )
        return self._persistent

    @property
    def runtime(self) -> Any:
        if self._runtime is None:
            from CloudFactoryManager.runtime import CursorCloudFactory

            self._runtime = CursorCloudFactory()
        return self._runtime

    def ensure_listen(self) -> None:
        if self._inbox is None:
            self._inbox = FieldInbox(self.ephemeral, wire.CLOUDORDER_CHANNEL)
        self._inbox.listen()

    def ensure_group(self) -> None:
        """Subscribe for execution signals. Name kept for existing callers."""
        self.ensure_listen()

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    # --- orders ---

    def poll_orders(self) -> int:
        """Drain published CLOUDORDER signals. Returns how many launched."""
        self.ensure_listen()
        assert self._inbox is not None
        for fields in self._inbox.drain():
            entry_id = str(self.ephemeral.xadd(wire.CLOUDORDER_STREAM, fields))
            self._pending.append((entry_id, fields))
        launched = 0
        still: list[tuple[str, dict[str, Any]]] = []
        for entry_id, fields in self._pending:
            if self.handle_order(entry_id, fields):
                launched += 1
                continue
            try:
                order_id = wire.parse_cloudorder(fields)["order_id"]
            except ValueError:
                continue
            if self._blocked_until.get(order_id, 0.0) > time.monotonic():
                still.append((entry_id, fields))
        self._pending = still
        return launched

    def handle_order(self, entry_id: str, fields: dict[str, Any]) -> bool:
        """Launch one order. Returns whether an agent now exists for it."""
        try:
            order = wire.parse_cloudorder(fields)
        except ValueError as exc:
            log.error("Unusable CLOUDORDER %s: %s", entry_id, exc)
            return False

        order_id = order["order_id"]
        if self.run_for_order(order_id) is not None:
            # Already launched. Launching again would open a second pull request.
            log.warning("Order %s already has a run; skipping", order_id)
            return False

        if self.settled_order(order_id) is not None:
            # Rejected (or otherwise finished) before we got to it — most
            # often from the FE reject button while this order sat in queue.
            log.info("Order %s already settled; skipping", order_id)
            return False

        wait_until = self._blocked_until.get(order_id, 0.0)
        if wait_until > time.monotonic():
            # Cursor asked for a delay. Leave it pending so it is retried on the
            # next pass rather than dropped.
            return False

        try:
            handle = self.runtime.launch(order)
        except AgentStartupError as exc:
            return self._launch_failed(entry_id, order, exc)
        except AgentError as exc:
            log.error("Order %s failed to launch: %s", order_id, exc)
            self._finish_without_agent(order, str(exc))
            return False

        # Rejected while Cursor was still minting the id: drop the agent rather
        # than registering a run the operator already refused.
        if self.settled_order(order_id) is not None:
            try:
                self.runtime.cancel(handle.run_key)
            except AgentError as exc:
                log.warning(
                    "Could not cancel rejected launch %s: %s", handle.run_key, exc
                )
            log.info(
                "Order %s was rejected during launch; dropped agent %s",
                order_id,
                handle.run_key,
            )
            return False

        # Registered as soon as Cursor mints an id, so a crash leaves a visible
        # run rather than an order that silently launched nothing.
        self.persistent.hset(
            wire.cloudrun_key(handle.run_key),
            mapping=wire.cloudrun_fields(
                order_id=order_id,
                repo_url=order["repo_url"],
                title=order["title"],
                status=wire.STATUS_RUNNING,
                run_id=handle.run_id,
            ),
        )
        self._attempts.pop(order_id, None)
        self._blocked_until.pop(order_id, None)
        log.info("Order %s is agent %s", order_id, handle.run_key)
        return True

    def _launch_failed(
        self, entry_id: str, order: dict[str, Any], exc: AgentStartupError
    ) -> bool:
        """A run that never started: retry only if Cursor said it was worth it."""
        order_id = order["order_id"]
        attempts = self._attempts.get(order_id, 0) + 1
        self._attempts[order_id] = attempts
        retryable = bool(getattr(exc, "retryable", False))

        if retryable and attempts < MAX_LAUNCH_ATTEMPTS:
            delay = float(getattr(exc, "retry_after", None) or self.retry_delay)
            self._blocked_until[order_id] = time.monotonic() + delay
            log.warning(
                "Order %s could not start (attempt %d/%d), retrying in %.1fs: %s",
                order_id,
                attempts,
                MAX_LAUNCH_ATTEMPTS,
                delay,
                exc,
            )
            return False

        log.error("Order %s could not start: %s", order_id, exc)
        self._finish_without_agent(order, str(exc))
        self._attempts.pop(order_id, None)
        return False

    def _finish_without_agent(self, order: dict[str, Any], detail: str) -> None:
        """Report a launch that produced no agent, so the FE stops waiting."""
        self.ephemeral.xadd(
            wire.CLOUDFINISHED_STREAM,
            wire.cloudfinished_fields(
                order_id=order["order_id"], status=wire.STATUS_STARTUP_ERROR
            ),
        )
        log.info("Order %s reported as %s: %s", order["order_id"], wire.STATUS_STARTUP_ERROR, detail)

    # --- runs ---

    def poll_runs(self, *, force: bool = False) -> int:
        """Ask Cursor about every live run. Returns how many handed off or failed."""
        now = time.monotonic()
        if not force and now < self._next_run_poll:
            return 0
        self._next_run_poll = now + self.run_poll_interval

        settled = 0
        already = self._finished_by_order()
        for agent_id, run in self.live_runs():
            done = already.get(run["order_id"])
            if done and done["status"] == wire.STATUS_CANCELLED:
                if self._honor_reject(agent_id, run):
                    settled += 1
                continue
            try:
                state = self.runtime.poll(agent_id)
            except AgentError as exc:
                log.warning("Could not read agent %s: %s", agent_id, exc)
                continue
            if self._apply_state(agent_id, run, state):
                settled += 1
        return settled

    def _honor_reject(self, agent_id: str, run: dict[str, str]) -> bool:
        """The FE rejected an order that already had a live agent."""
        try:
            self.runtime.cancel(agent_id)
        except AgentError as exc:
            log.warning("Could not cancel rejected agent %s: %s", agent_id, exc)
            return False
        self.persistent.hset(
            wire.cloudrun_key(agent_id), "status", wire.STATUS_CANCELLED
        )
        log.info("Agent %s cancelled after order %s was rejected", agent_id, run["order_id"])
        return True

    def _apply_state(self, agent_id: str, run: dict[str, str], state: Any) -> bool:
        status = str(getattr(state, "status", "") or wire.STATUS_RUNNING)
        # ``result`` is the shared name for whatever a run produced; here it is
        # a pull request URL — a VM kill switch, not a MegaDesk done flag.
        pr_url = str(getattr(state, "result", "") or "")

        # Already handed off. A later poll may come back cancelled because we
        # reaped the VM; that must not write a second stream entry.
        if run["pr_url"]:
            return False

        if pr_url:
            # First PR URL: leave status running, store the URL, cancel the VM
            # so a later commit cannot open a second PR. Do not XADD
            # CLOUDFINISHED — success lives on GitHub. Call runtime.cancel,
            # not self.cancel: the manager method writes cancelled and would
            # look like the operator killed a successful run.
            self.persistent.hset(
                wire.cloudrun_key(agent_id),
                mapping={"pr_url": pr_url},
            )
            try:
                self.runtime.cancel(agent_id)
            except AgentError as exc:
                log.warning("Could not cancel handed-off agent %s: %s", agent_id, exc)
            log.info("Agent %s handed off to GitHub -> %s", agent_id, pr_url)
            return True

        if status == wire.STATUS_FINISHED:
            # Runtime must not return this; a success with no PR is a failed run.
            status = wire.STATUS_ERROR

        changed = status != run["status"]
        if changed:
            self.persistent.hset(
                wire.cloudrun_key(agent_id),
                mapping={"status": status},
            )
        if status not in {
            wire.STATUS_ERROR,
            wire.STATUS_CANCELLED,
            wire.STATUS_STARTUP_ERROR,
        }:
            return False

        if status == wire.STATUS_ERROR:
            try:
                self.runtime.cancel(agent_id)
            except AgentError as exc:
                log.warning("Could not cancel failed agent %s: %s", agent_id, exc)

        self.ephemeral.xadd(
            wire.CLOUDFINISHED_STREAM,
            wire.cloudfinished_fields(
                agent_id=agent_id,
                order_id=run["order_id"],
                status=status,
            ),
        )
        log.info("Agent %s %s", agent_id, status)
        return True

    def live_runs(self) -> list[tuple[str, dict[str, str]]]:
        """Runs still on a Cursor VM: not failed, not cancelled, not handed off."""
        out: list[tuple[str, dict[str, str]]] = []
        for key in self.persistent.scan_iter(
            match=f"{wire.CLOUDRUN_PREFIX}*", count=100
        ):
            fields = self.persistent.hgetall(key)
            try:
                run = wire.parse_cloudrun(fields)
            except ValueError as exc:
                log.warning("Unusable %s: %s", key, exc)
                continue
            if run["status"] in wire.TERMINAL_STATUSES or run["pr_url"]:
                continue
            out.append((wire.agent_id_from_key(key), run))
        return out

    def run_for_order(self, order_id: str) -> Optional[str]:
        """The agent id launched for an order, if one already was."""
        for key in self.persistent.scan_iter(
            match=f"{wire.CLOUDRUN_PREFIX}*", count=100
        ):
            if self.persistent.hget(key, "order_id") == order_id:
                return wire.agent_id_from_key(key)
        return None

    def _finished_by_order(self) -> dict[str, dict[str, str]]:
        by_order: dict[str, dict[str, str]] = {}
        entries = self.ephemeral.xrevrange(wire.CLOUDFINISHED_STREAM, count=200)
        for _entry_id, fields in entries or []:
            try:
                parsed = wire.parse_cloudfinished(fields)
            except ValueError:
                continue
            by_order.setdefault(parsed["order_id"], parsed)
        return by_order

    def settled_order(self, order_id: str) -> Optional[dict[str, str]]:
        """The newest CLOUDFINISHED for this order, if it already has one."""
        return self._finished_by_order().get(order_id)

    def reject(self, order_id: str) -> bool:
        """Refuse an order. Unlaunched orders finish as cancelled with no agent."""
        order_id = str(order_id or "").strip()
        if not order_id:
            return False
        if self.settled_order(order_id) is not None:
            return False
        agent_id = self.run_for_order(order_id)
        if agent_id:
            return self.cancel(agent_id)
        self.ephemeral.xadd(
            wire.CLOUDFINISHED_STREAM,
            wire.cloudfinished_fields(
                order_id=order_id, status=wire.STATUS_CANCELLED
            ),
        )
        log.info("Order %s rejected", order_id)
        return True

    def cancel(self, agent_id: str) -> bool:
        key = wire.cloudrun_key(agent_id)
        if not self.persistent.exists(key):
            return False
        try:
            self.runtime.cancel(agent_id)
        except AgentError as exc:
            log.warning("Could not cancel %s: %s", agent_id, exc)
            return False
        run = wire.parse_cloudrun(self.persistent.hgetall(key))
        self.persistent.hset(key, "status", wire.STATUS_CANCELLED)
        self.ephemeral.xadd(
            wire.CLOUDFINISHED_STREAM,
            wire.cloudfinished_fields(
                agent_id=agent_id,
                order_id=run["order_id"],
                status=wire.STATUS_CANCELLED,
            ),
        )
        return True

    # --- loop ---

    def poll_once(self) -> int:
        return self.poll_orders() + self.poll_runs()

    def run_forever(self) -> None:
        log.info(
            "CloudFactoryManager up: listening on %s (stream %s is reference only)",
            wire.CLOUDORDER_CHANNEL,
            wire.CLOUDORDER_STREAM,
        )
        from megadesk_contracts import node_should_stop

        try:
            while not node_should_stop():
                try:
                    self.poll_once()
                    time.sleep(self.poll_interval)
                except KeyboardInterrupt:
                    log.info("Interrupted; shutting down")
                    return
                except Exception:  # noqa: BLE001 - a long-lived BE outlives Redis restarts
                    log.exception("Poll failed; retrying in %.1fs", RECONNECT_WAIT_SEC)
                    if self._inbox is not None:
                        self._inbox.close()
                        self._inbox = None
                    time.sleep(RECONNECT_WAIT_SEC)
        finally:
            closer = getattr(self._runtime, "close", None)
            if callable(closer):
                closer()
            if self._inbox is not None:
                self._inbox.close()
                self._inbox = None


def main() -> None:
    CloudFactoryManager().run_forever()


if __name__ == "__main__":
    main()
