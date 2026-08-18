"""Consume WORKORDER, prepare a worktree, run the agent in a local sandbox.

Two loops, the same two CloudFactory runs, for the same reason: orders arrive in
bursts and start in under a second, while the runs they start take minutes and
outlive any particular manager process. So the AGENTHANDLER hashes on Redis are
the source of truth rather than anything held in memory.

* ``poll_orders`` reads the consumer group, prepares the Floor, writes
  ``AGENTHANDLER:<guid>`` and only then starts the sandbox.
* ``poll_runs`` walks that registry looking for runs whose sandbox is gone.

The order of the first loop is what makes the handshake work: the container finds
its own work by reading the hash, so the hash must exist before it starts. That is
the mirror image of the cloud, where the provider mints the id and the registry
entry can only be written afterwards.

The second loop exists because a container is not a managed service. A healthy
sandbox reports its own outcome onto ``FINISHED:<REPO>`` and deletes its hash on
the way out — from inside, where the exit code is. ``poll_runs`` only covers the
case where it never got the chance, which would otherwise leave a hash claiming a
run that stopped existing and a ticket nobody merges.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import ResponseError
from redis.exceptions import TimeoutError as RedisTimeoutError

from megadesk_contracts import resolve_redis_url
from megadesk_contracts.wire import machine as wire

from MachineFactoryManager.floor import (
    agents_worktree,
    create_ticket_worktree,
    default_floor,
    ensure_repo,
    safe_repo_name,
    ticket_worktree,
)
from MachineFactoryManager.pool import LOCAL_REDIS_URL

log = logging.getLogger("manager")

POLL_INTERVAL_SEC = 1.0
WORKORDER_BATCH = 32
# Docker is cheap to ask but not free, and nothing here changes second to second.
RUN_POLL_INTERVAL_SEC = 10.0
# A sandbox publishes FINISHED and then deletes its hash. For the moment between
# those two, its container is already gone while its hash is still there — so a
# run is only treated as lost once it has been missing for longer than that.
ORPHAN_GRACE_SEC = 30.0


def connect_redis(redis_url: str | None = None) -> Redis:
    url = redis_url or resolve_redis_url()
    client = Redis.from_url(
        url,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=None,
    )
    try:
        client.ping()
    except (RedisConnectionError, RedisTimeoutError, OSError) as exc:
        raise SystemExit(
            f"Failed to connect to Redis at {url}. "
            "Start a local Redis server and retry."
        ) from exc
    return client


def ensure_workorder_group(redis: Redis) -> None:
    """Create the WORKORDER consumer group if it does not already exist."""
    try:
        redis.xgroup_create(
            wire.WORKORDER_STREAM,
            wire.WORKORDER_GROUP,
            id="0",
            mkstream=True,
        )
        log.info(
            "Created consumer group %s on stream %s",
            wire.WORKORDER_GROUP,
            wire.WORKORDER_STREAM,
        )
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


class MachineFactoryManager:
    """Turn orders into sandboxed agents, and lost sandboxes into FINISHED."""

    def __init__(
        self,
        *,
        redis_url: Optional[str] = None,
        redis: Any = None,
        runtime: Any = None,
        group: str = wire.WORKORDER_GROUP,
        consumer: Optional[str] = None,
        floor: Optional[Path] = None,
        run_poll_interval: float = RUN_POLL_INTERVAL_SEC,
        orphan_grace: float = ORPHAN_GRACE_SEC,
    ) -> None:
        self.redis_url = redis_url or os.environ.get("REDIS_URL", LOCAL_REDIS_URL)
        self.floor = floor or default_floor()
        self.group = group
        self._running = True
        self.consumer = consumer or os.environ.get(
            "MACHINE_FACTORY_CONSUMER", f"machine_factory-{os.getpid()}"
        )
        self.run_poll_interval = float(run_poll_interval)
        self.orphan_grace = float(orphan_grace)
        self._redis = redis
        self._runtime = runtime
        self._next_run_poll = 0.0
        self._missing_since: dict[str, float] = {}

    def stop(self, *_args: object) -> None:
        log.info("Shutdown signal received")
        self._running = False

    @property
    def redis(self) -> Any:
        if self._redis is None:
            self._redis = connect_redis(self.redis_url)
        return self._redis

    @property
    def runtime(self) -> Any:
        if self._runtime is None:
            from MachineFactoryManager.runtime import DockerSandboxFactory

            self._runtime = DockerSandboxFactory()
        return self._runtime

    # --- registry ---

    def _write_agent_handler(
        self,
        redis: Redis,
        *,
        guid: str,
        ticket_id: str,
        status: str = "",
        error: str = "",
    ) -> str:
        key = wire.agent_handler_key(guid)
        redis.hset(
            key,
            mapping=wire.agent_handler_fields(
                ticket_id=ticket_id,
                status=status,
                error=error,
            ),
        )
        log.info("Wrote %s ticket_id=%s", key, ticket_id)
        return key

    def _finish(
        self,
        redis: Redis,
        *,
        guid: str,
        repo: str,
        ticket_name: str,
        ticket_id: str,
        workpath: Path,
        agent_dir: Path,
        error: str,
    ) -> None:
        """Report a run that produced nothing, so nobody waits on it forever."""
        key = wire.agent_handler_key(guid)
        redis.hset(
            key,
            mapping=wire.agent_handler_fields(
                ticket_id=ticket_id,
                status=wire.STATUS_ERROR,
                error=error,
            ),
        )
        payload = wire.finished_fields(
            ticket_name=ticket_name,
            ticket_id=ticket_id,
            wt=str(workpath),
            agent_dir=str(agent_dir),
        )
        stream = wire.finished_stream(repo)
        redis.xadd(stream, payload)
        redis.delete(key)
        self._missing_since.pop(guid, None)
        log.error("Published %s and deleted %s: %s", stream, key, error)

    # --- orders ---

    def handle_workorder(
        self,
        redis: Redis,
        message_id: str,
        fields: dict[str, Any],
    ) -> bool:
        """Prepare the Floor and start one sandbox. False when nothing started."""
        item = wire.parse_workorder(fields)
        repo = safe_repo_name(item["repo"])
        ticket_name = item["ticket_name"]
        url = item["URL"]
        new_wt = bool(item["new_wt"])
        log.info(
            "WORKORDER %s: repo=%s ticket=%s new_wt=%s url=%s",
            message_id,
            repo,
            ticket_name,
            new_wt,
            url,
        )

        try:
            if new_wt:
                if not url:
                    raise ValueError(
                        "WORKORDER with new_wt=true requires URL to create Floor repo"
                    )
                ensure_repo(repo=repo, url=url, floor_root=self.floor)
                workpath = create_ticket_worktree(repo, ticket_name, self.floor)
            else:
                workpath = Path(item["wt"])
                if not workpath.is_absolute():
                    raise ValueError(f"WORKORDER wt must be absolute: {workpath}")
                if not workpath.exists():
                    raise FileNotFoundError(f"WORKORDER wt does not exist: {workpath}")
                # Ensure Floor agents tree exists when URL is provided.
                if url:
                    ensure_repo(repo=repo, url=url, floor_root=self.floor)
                elif not (self.floor / repo / ".bare").exists():
                    raise FileNotFoundError(
                        f"Repo {repo!r} missing under Floor and no URL on WORKORDER"
                    )
        except (
            ValueError,
            FileNotFoundError,
            OSError,
            subprocess.CalledProcessError,
        ) as exc:
            log.error(
                "WORKORDER Floor setup failed for %s/%s: %s",
                repo,
                ticket_name,
                exc,
            )
            return False

        agent_dir = agents_worktree(repo, self.floor)
        if not agent_dir.exists():
            log.error("agents worktree missing at %s", agent_dir)
            return False

        guid = str(uuid.uuid4())
        self._write_agent_handler(
            redis,
            guid=guid,
            ticket_id=message_id,
            status=wire.STATUS_QUEUED,
        )

        try:
            handle = self.runtime.launch(
                {
                    "run_key": guid,
                    "repo": repo,
                    "ticket_name": ticket_name,
                    "instructions": item["instructions"],
                    "model": item["model"],
                    "wt": str(workpath),
                    "agent_dir": str(agent_dir),
                    "ticket_id": message_id,
                }
            )
        except SystemExit:
            raise
        except Exception as exc:  # noqa: BLE001
            log.error("Failed to start sandbox for %s/%s: %s", repo, ticket_name, exc)
            self._finish(
                redis,
                guid=guid,
                repo=repo,
                ticket_name=ticket_name,
                ticket_id=message_id,
                workpath=workpath,
                agent_dir=agent_dir,
                error=str(exc),
            )
            return False

        redis.hset(wire.agent_handler_key(guid), "status", wire.STATUS_RUNNING)
        log.info(
            "WORKORDER sandbox started container=%s guid=%s ticket_id=%s wt=%s",
            handle.run_id,
            guid,
            message_id,
            workpath,
        )
        return True

    def _read_workorders(
        self, redis: Redis, *, pending: bool
    ) -> list[tuple[str, dict[str, Any]]]:
        stream_id = "0" if pending else ">"
        results = redis.xreadgroup(
            groupname=self.group,
            consumername=self.consumer,
            streams={wire.WORKORDER_STREAM: stream_id},
            count=WORKORDER_BATCH,
        )
        entries: list[tuple[str, dict[str, Any]]] = []
        if not results:
            return entries
        for _stream, messages in results:
            for message_id, fields in messages:
                entries.append((message_id, fields))
        return entries

    def _process_workorder_entry(
        self, redis: Redis, message_id: str, fields: dict[str, Any]
    ) -> bool:
        started = False
        try:
            started = self.handle_workorder(redis, message_id, fields)
        except ValueError as exc:
            log.error("Bad WORKORDER entry %s: %s fields=%r", message_id, exc, fields)
        except SystemExit:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Unhandled error processing WORKORDER %s", message_id)

        # Acked either way: a poison entry retried forever would block the group
        # behind it, and the failure has already been reported where it belongs.
        redis.xack(wire.WORKORDER_STREAM, self.group, message_id)
        return started

    def ensure_group(self) -> None:
        ensure_workorder_group(self.redis)

    def poll_orders(self) -> int:
        """Start a sandbox per order. Returns how many sandboxes started."""
        redis = self.redis
        started = 0
        for pending in (True, False):
            for message_id, fields in self._read_workorders(redis, pending=pending):
                if self._process_workorder_entry(redis, message_id, fields):
                    started += 1
        return started

    # --- runs ---

    def live_runs(self) -> list[tuple[str, dict[str, str]]]:
        """Every run the registry still claims, keyed by sandbox guid."""
        out: list[tuple[str, dict[str, str]]] = []
        for key in self.redis.scan_iter(
            match=f"{wire.AGENTHANDLER_PREFIX}*", count=100
        ):
            try:
                run = wire.parse_agent_handler(self.redis.hgetall(key))
                out.append((wire.guid_from_agent_handler_key(key), run))
            except ValueError as exc:
                log.warning("Unusable %s: %s", key, exc)
        return out

    def poll_runs(self, *, force: bool = False) -> int:
        """Reap runs whose sandbox is gone. Returns how many were reaped."""
        now = time.monotonic()
        if not force and now < self._next_run_poll:
            return 0
        self._next_run_poll = now + self.run_poll_interval

        seen: set[str] = set()
        reaped = 0
        for guid, run in self.live_runs():
            seen.add(guid)
            try:
                state = self.runtime.poll(guid)
            except Exception as exc:  # noqa: BLE001
                log.warning("Could not read sandbox for run %s: %s", guid, exc)
                continue
            if str(getattr(state, "status", "")) == wire.STATUS_RUNNING:
                self._missing_since.pop(guid, None)
                continue
            first_missing = self._missing_since.setdefault(guid, now)
            if now - first_missing < self.orphan_grace:
                continue
            if self._reap(guid, run):
                reaped += 1
        self._missing_since = {
            guid: at for guid, at in self._missing_since.items() if guid in seen
        }
        return reaped

    def _reap(self, guid: str, run: dict[str, str]) -> bool:
        """Publish FINISHED for a run whose sandbox died without reporting."""
        ticket_id = run["ticket_id"]
        try:
            order = wire.load_workorder(self.redis, ticket_id)
        except (LookupError, ValueError) as exc:
            # No order to name the ticket with, so nothing coherent to publish.
            # Drop the hash rather than re-checking a dead sandbox forever.
            log.error("Run %s lost its order %s (%s); dropping", guid, ticket_id, exc)
            self.redis.delete(wire.agent_handler_key(guid))
            self._missing_since.pop(guid, None)
            return False

        repo = safe_repo_name(order["repo"])
        ticket_name = order["ticket_name"]
        workpath = (
            ticket_worktree(repo, ticket_name, self.floor)
            if order["new_wt"]
            else Path(order["wt"])
        )
        self._finish(
            self.redis,
            guid=guid,
            repo=repo,
            ticket_name=ticket_name,
            ticket_id=ticket_id,
            workpath=workpath,
            agent_dir=agents_worktree(repo, self.floor),
            error="sandbox stopped without publishing FINISHED",
        )
        return True

    def cancel(self, guid: str) -> bool:
        """Stop a run and report it, so the ticket does not sit in limbo."""
        key = wire.agent_handler_key(guid)
        fields = self.redis.hgetall(key)
        if not fields:
            return False
        try:
            self.runtime.cancel(guid)
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not cancel run %s: %s", guid, exc)
            return False
        run = wire.parse_agent_handler(fields)
        self.redis.hset(key, "status", wire.STATUS_CANCELLED)
        self._missing_since[guid] = 0.0
        log.info("Cancelled run %s (ticket_id=%s)", guid, run["ticket_id"])
        return True

    # --- loop ---

    def poll_once(self) -> int:
        return self.poll_orders() + self.poll_runs()

    def run_forever(self) -> None:
        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)

        redis = self.redis
        ensure_workorder_group(redis)
        log.info(
            "MachineFactoryManager polling stream %s (group=%s consumer=%s) floor=%s redis=%s",
            wire.WORKORDER_STREAM,
            self.group,
            self.consumer,
            self.floor,
            self.redis_url,
        )

        from megadesk_contracts import node_should_stop

        while self._running and not node_should_stop():
            try:
                self.poll_once()
            except SystemExit:
                raise
            except Exception:  # noqa: BLE001
                log.exception("Unhandled error in poll loop")
            time.sleep(POLL_INTERVAL_SEC)

        log.info("MachineFactoryManager stopped")


def main() -> None:
    MachineFactoryManager().run_forever()
