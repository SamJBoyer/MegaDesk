"""Consume WORKORDER, start a clone-in-sandbox agent with a Redis sidecar.

Two loops, the same two CloudFactory runs, for the same reason: orders arrive in
bursts and start in under a second, while the runs they start take minutes and
outlive any particular manager process. So the AGENTHANDLER hashes on Redis are
the source of truth rather than anything held in memory.

* ``poll_orders`` reads the consumer group, writes ``AGENTHANDLER:<guid>`` and
  only then starts the sandbox (agent container + Redis sidecar).
* ``poll_runs`` walks that registry looking for runs whose sandbox is gone.

The order of the first loop is what makes the handshake work: the container finds
its own work by reading the hash, so the hash must exist before it starts.

The second loop exists because a container is not a managed service. A healthy
sandbox reports its own outcome onto ``FINISHED:<REPO>`` and deletes its hash on
the way out. ``poll_runs`` only covers the case where it never got the chance.
"""

from __future__ import annotations

import logging
import os
import signal
import time
import uuid
from typing import Any, Optional

from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import ResponseError
from redis.exceptions import TimeoutError as RedisTimeoutError

from megadesk_contracts import redis_connect, resolve_ephemeral_db, resolve_redis_url
from megadesk_contracts.repo import safe_repo_name
from megadesk_contracts.wire import machine as wire

from MachineFactoryManager.pool import LOCAL_REDIS_URL

log = logging.getLogger("manager")

POLL_INTERVAL_SEC = 1.0
WORKORDER_BATCH = 32
RUN_POLL_INTERVAL_SEC = 10.0
ORPHAN_GRACE_SEC = 30.0


def connect_redis(redis_url: str | None = None) -> Redis:
    url = redis_url or resolve_redis_url()
    client = redis_connect(
        url,
        db=resolve_ephemeral_db(url),
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
        run_poll_interval: float = RUN_POLL_INTERVAL_SEC,
        orphan_grace: float = ORPHAN_GRACE_SEC,
    ) -> None:
        self.redis_url = redis_url or os.environ.get("REDIS_URL", LOCAL_REDIS_URL)
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
        status: str,
        error: str,
        pr_url: str = "",
    ) -> None:
        """Report a run that produced nothing, so nobody waits on it forever."""
        key = wire.agent_handler_key(guid)
        redis.hset(
            key,
            mapping=wire.agent_handler_fields(
                ticket_id=ticket_id,
                status=status,
                error=error,
            ),
        )
        payload = wire.finished_fields(
            ticket_name=ticket_name,
            ticket_id=ticket_id,
            status=status,
            pr_url=pr_url,
        )
        stream = wire.finished_stream(repo)
        redis.xadd(stream, payload)
        redis.delete(key)
        self._missing_since.pop(guid, None)
        log.error("Published %s and deleted %s: %s", stream, key, error or status)

    def handle_workorder(
        self,
        redis: Redis,
        message_id: str,
        fields: dict[str, Any],
    ) -> bool:
        """Start one sandbox for a WORKORDER. False when nothing started."""
        item = wire.parse_workorder(fields)
        repo = safe_repo_name(item["repo"])
        ticket_name = item["ticket_name"]
        url = item["URL"]
        log.info(
            "WORKORDER %s: repo=%s ticket=%s url=%s auto_pr=%s",
            message_id,
            repo,
            ticket_name,
            url,
            item["auto_pr"],
        )
        if not url:
            log.error("WORKORDER %s missing URL; cannot clone", message_id)
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
                    "URL": url,
                    "auto_pr": item["auto_pr"],
                    "ticket_id": message_id,
                }
            )
        except SystemExit:
            raise
        except Exception as exc:  # noqa: BLE001
            log.error("Failed to start sandbox for %s/%s: %s", repo, ticket_name, exc)
            releaser = getattr(self.runtime, "release", None)
            if callable(releaser):
                try:
                    releaser(guid)
                except Exception:  # noqa: BLE001
                    pass
            self._finish(
                redis,
                guid=guid,
                repo=repo,
                ticket_name=ticket_name,
                ticket_id=message_id,
                status=wire.STATUS_ERROR,
                error=str(exc),
            )
            return False

        redis.hset(wire.agent_handler_key(guid), "status", wire.STATUS_RUNNING)
        log.info(
            "WORKORDER sandbox started container=%s guid=%s ticket_id=%s",
            handle.run_id,
            guid,
            message_id,
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
        releaser = getattr(self.runtime, "release", None)
        if callable(releaser):
            try:
                releaser(guid)
            except Exception as exc:  # noqa: BLE001
                log.warning("Could not release Redis sidecar for run %s: %s", guid, exc)
        ticket_id = run["ticket_id"]
        try:
            order = wire.load_workorder(self.redis, ticket_id)
        except (LookupError, ValueError) as exc:
            log.error("Run %s lost its order %s (%s); dropping", guid, ticket_id, exc)
            self.redis.delete(wire.agent_handler_key(guid))
            self._missing_since.pop(guid, None)
            return False

        repo = safe_repo_name(order["repo"])
        ticket_name = order["ticket_name"]
        self._finish(
            self.redis,
            guid=guid,
            repo=repo,
            ticket_name=ticket_name,
            ticket_id=ticket_id,
            status=wire.STATUS_ERROR,
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

    def poll_once(self) -> int:
        return self.poll_orders() + self.poll_runs()

    def run_forever(self) -> None:
        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)

        redis = self.redis
        ensure_workorder_group(redis)
        log.info(
            "MachineFactoryManager polling stream %s (group=%s consumer=%s) redis=%s",
            wire.WORKORDER_STREAM,
            self.group,
            self.consumer,
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
