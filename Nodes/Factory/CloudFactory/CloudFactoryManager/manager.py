"""Consume CLOUDORDER, launch cloud agents, hand a PR off to GitHub.

Two loops, the same two MachineFactory runs, because the two halves have very
different clocks. Orders arrive in bursts and launch in under a second; the runs
they start take minutes, and the BE may be restarted while they are still going.
So the registry on the persistent DB is the source of truth rather than anything held in
memory:

* ``poll_orders`` reads the consumer group, launches, and writes
  ``CLOUDRUN:<agent_id>`` before it does anything else it could fail at.
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

log = logging.getLogger("cloud_factory.manager")

ORDER_BATCH = 16
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
        self._group_ready = False
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

    def ensure_group(self) -> None:
        if self._group_ready:
            return
        from redis.exceptions import ResponseError

        try:
            self.ephemeral.xgroup_create(
                wire.CLOUDORDER_STREAM, self.group, id="0", mkstream=True
            )
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        self._group_ready = True

    # --- orders ---

    def poll_orders(self) -> int:
        """Drain pending then new CLOUDORDER entries. Returns how many launched."""
        self.ensure_group()
        launched = 0
        for stream_id in ("0", ">"):
            results = self._xreadgroup(stream_id)
            for _stream, messages in results or []:
                for entry_id, fields in messages:
                    if self.handle_order(entry_id, fields):
                        launched += 1
        return launched

    def _xreadgroup(self, stream_id: str) -> Any:
        """Read the group, recreating it if Redis was flushed out from under us."""
        from redis.exceptions import ResponseError

        kwargs = {
            "groupname": self.group,
            "consumername": self.consumer,
            "streams": {wire.CLOUDORDER_STREAM: stream_id},
            "count": ORDER_BATCH,
        }
        try:
            return self.ephemeral.xreadgroup(**kwargs)
        except ResponseError as exc:
            if "NOGROUP" not in str(exc):
                raise
            self._group_ready = False
            self.ensure_group()
            return self.ephemeral.xreadgroup(**kwargs)

    def handle_order(self, entry_id: str, fields: dict[str, Any]) -> bool:
        """Launch one order. Returns whether an agent now exists for it."""
        try:
            order = wire.parse_cloudorder(fields)
        except ValueError as exc:
            log.error("Unusable CLOUDORDER %s: %s", entry_id, exc)
            self._ack(entry_id)
            return False

        order_id = order["order_id"]
        if self.run_for_order(order_id) is not None:
            # Already launched, and the ack evidently did not land. Launching
            # again would open a second pull request for one request.
            log.warning("Order %s already has a run; skipping", order_id)
            self._ack(entry_id)
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
            self._ack(entry_id)
            return False

        # Registered before the ack, so a crash in between leaves a visible run
        # rather than an order that silently launched nothing.
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
        self._ack(entry_id)
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
        self._ack(entry_id)
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
        for agent_id, run in self.live_runs():
            try:
                state = self.runtime.poll(agent_id)
            except AgentError as exc:
                log.warning("Could not read agent %s: %s", agent_id, exc)
                continue
            if self._apply_state(agent_id, run, state):
                settled += 1
        return settled

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
            "CloudFactoryManager up: %s group=%s consumer=%s",
            wire.CLOUDORDER_STREAM,
            self.group,
            self.consumer,
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
                    self._group_ready = False
                    time.sleep(RECONNECT_WAIT_SEC)
        finally:
            closer = getattr(self._runtime, "close", None)
            if callable(closer):
                closer()

    def _ack(self, entry_id: str) -> None:
        self.ephemeral.xack(wire.CLOUDORDER_STREAM, self.group, entry_id)


def main() -> None:
    CloudFactoryManager().run_forever()


if __name__ == "__main__":
    main()
