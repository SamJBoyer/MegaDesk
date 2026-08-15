"""Long-running MissionControlManager: poll WORKORDER stream, prepare Floor, start sandboxes."""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import ResponseError
from redis.exceptions import TimeoutError as RedisTimeoutError

from MissionControlManager.floor import (
    agents_worktree,
    create_ticket_worktree,
    default_floor,
    ensure_repo,
    safe_repo_name,
)
from MissionControlManager.pool import LOCAL_REDIS_URL, start_ticket_sandbox
from redis_packets import (
    WORKORDER_STREAM,
    finished_fields,
    finished_stream,
    agent_handler_fields,
    agent_handler_key,
    parse_workorder,
)

log = logging.getLogger("manager")

WORKORDER_GROUP = "mission_control"
POLL_INTERVAL_SEC = 1.0
WORKORDER_BATCH = 32


def connect_redis(redis_url: str | None = None) -> Redis:
    url = redis_url or os.environ.get("REDIS_URL", LOCAL_REDIS_URL)
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
            WORKORDER_STREAM,
            WORKORDER_GROUP,
            id="0",
            mkstream=True,
        )
        log.info(
            "Created consumer group %s on stream %s",
            WORKORDER_GROUP,
            WORKORDER_STREAM,
        )
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


class MissionControlManager:
    def __init__(self) -> None:
        self.redis_url = os.environ.get("REDIS_URL", LOCAL_REDIS_URL)
        self.floor = default_floor()
        self._running = True
        self.consumer = os.environ.get(
            "MISSION_CONTROL_CONSUMER", f"mission_control-{os.getpid()}"
        )

    def stop(self, *_args: object) -> None:
        log.info("Shutdown signal received")
        self._running = False

    def _write_agent_handler(
        self,
        redis: Redis,
        *,
        guid: str,
        ticket_id: str,
        status: str = "",
        error: str = "",
    ) -> str:
        key = agent_handler_key(guid)
        redis.hset(
            key,
            mapping=agent_handler_fields(
                ticket_id=ticket_id,
                status=status,
                error=error,
            ),
        )
        log.info("Wrote %s ticket_id=%s", key, ticket_id)
        return key

    def _fail_agent_handler(
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
        """Publish FINISHED stream entry and delete AGENTHANDLER when sandbox fails."""
        key = agent_handler_key(guid)
        redis.hset(
            key,
            mapping=agent_handler_fields(
                ticket_id=ticket_id,
                status="error",
                error=error,
            ),
        )
        payload = finished_fields(
            ticket_name=ticket_name,
            ticket_id=ticket_id,
            wt=str(workpath),
            agent_dir=str(agent_dir),
        )
        stream = finished_stream(repo)
        redis.xadd(stream, payload)
        redis.delete(key)
        log.error(
            "Published %s and deleted %s after sandbox failure: %s",
            stream,
            key,
            error,
        )

    def handle_workorder(
        self,
        redis: Redis,
        message_id: str,
        fields: dict[str, Any],
    ) -> None:
        item = parse_workorder(fields)
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
            return

        agent_dir = agents_worktree(repo, self.floor)
        if not agent_dir.exists():
            log.error("agents worktree missing at %s", agent_dir)
            return

        guid = str(uuid.uuid4())
        self._write_agent_handler(
            redis,
            guid=guid,
            ticket_id=message_id,
            status="starting",
        )

        try:
            container = start_ticket_sandbox(
                repo=repo,
                ticket=ticket_name,
                host_worktree=workpath,
                agent_dir=agent_dir,
                guid=guid,
                ticket_id=message_id,
            )
        except SystemExit:
            raise
        except Exception as exc:  # noqa: BLE001
            log.error("Failed to start sandbox for %s/%s: %s", repo, ticket_name, exc)
            self._fail_agent_handler(
                redis,
                guid=guid,
                repo=repo,
                ticket_name=ticket_name,
                ticket_id=message_id,
                workpath=workpath,
                agent_dir=agent_dir,
                error=str(exc),
            )
            return

        log.info(
            "WORKORDER sandbox started container=%s guid=%s ticket_id=%s wt=%s",
            container,
            guid,
            message_id,
            workpath,
        )

    def _read_workorders(
        self, redis: Redis, *, pending: bool
    ) -> list[tuple[str, dict[str, Any]]]:
        stream_id = "0" if pending else ">"
        results = redis.xreadgroup(
            groupname=WORKORDER_GROUP,
            consumername=self.consumer,
            streams={WORKORDER_STREAM: stream_id},
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
    ) -> None:
        try:
            self.handle_workorder(redis, message_id, fields)
        except ValueError as exc:
            log.error("Bad WORKORDER entry %s: %s fields=%r", message_id, exc, fields)
        except SystemExit:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Unhandled error processing WORKORDER %s", message_id)
        else:
            redis.xack(WORKORDER_STREAM, WORKORDER_GROUP, message_id)
            return

        # Ack poison / invalid messages so they are not retried forever.
        redis.xack(WORKORDER_STREAM, WORKORDER_GROUP, message_id)

    def poll_once(self, redis: Redis) -> None:
        for message_id, fields in self._read_workorders(redis, pending=True):
            self._process_workorder_entry(redis, message_id, fields)

        for message_id, fields in self._read_workorders(redis, pending=False):
            self._process_workorder_entry(redis, message_id, fields)

    def run_forever(self) -> None:
        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)

        redis = connect_redis(self.redis_url)
        ensure_workorder_group(redis)
        log.info(
            "MissionControlManager polling stream %s (group=%s consumer=%s) floor=%s redis=%s",
            WORKORDER_STREAM,
            WORKORDER_GROUP,
            self.consumer,
            self.floor,
            self.redis_url,
        )

        from megadesk_contracts import node_should_stop

        while self._running and not node_should_stop():
            try:
                self.poll_once(redis)
            except SystemExit:
                raise
            except Exception:  # noqa: BLE001
                log.exception("Unhandled error in poll loop")
            time.sleep(POLL_INTERVAL_SEC)

        log.info("MissionControlManager stopped")


def main() -> None:
    MissionControlManager().run_forever()
