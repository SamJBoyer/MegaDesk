"""Thin Redis stream client for Supervisor LAUNCHREQUEST / KILLREQUEST.

A MegaDesk process occupies a Redis *pair*: ephemeral streams and persistent
hashes. The live pair is always ``(0, 1)``. ``REDIS_URL`` names the ephemeral
index of *this* process; persistent is ephemeral + 1, except URLs that name
db 0 or 1 stay on the live pair. See ``resolve_redis_pair``.

Connection standard: ``REDIS_URL`` (default ``redis://localhost:6379/0``).
Ephemeral vs persistent is selected via the ``db`` argument on ``Redis.from_url``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Mapping, Optional
from urllib.parse import urlparse, urlunparse

import redis


DEFAULT_REDIS_URL = "redis://localhost:6379/0"
REDIS_DB_EPHEMERAL = 0
REDIS_DB_PERSISTENT = 1
HOST_PYTEST_EPHEMERAL_DB = 14
HOST_PYTEST_PERSISTENT_DB = 15
ENV_FACTORY_REDIS_URL = "MEGADESK_FACTORY_REDIS_URL"
ENV_DEV_FLUSH_MODE = "DEV_FLUSH_MODE"
_DEV_FLUSH_MODE_TRUTHY = frozenset({"1", "true", "yes", "on"})
SUPERVISOR_ALIVE_KEY = "SUPERVISOR:ALIVE"
SUPERVISOR_SINGLETON_KEY = "SUPERVISOR:SINGLETON"
SUPERVISOR_NODE_NAME = "supervisor"
LAUNCHREQUEST_STREAM = "SUPERVISOR:LAUNCHREQUEST"
KILLREQUEST_STREAM = "SUPERVISOR:KILLREQUEST"
NODEEXIT_STREAM = "NODEEXIT"
RUNNINGNODES_PREFIX = "RUNNINGNODES:"


def resolve_redis_url(redis_url: Optional[str] = None) -> str:
    """Resolve the Redis URL: explicit arg, else ``REDIS_URL``, else default."""
    raw = redis_url if redis_url is not None else os.environ.get("REDIS_URL")
    text = (raw or DEFAULT_REDIS_URL).strip()
    return text or DEFAULT_REDIS_URL


def resolve_factory_redis_url(redis_url: Optional[str] = None) -> str:
    """Redis URL for factory IPC (WORKORDER / AGENTHANDLER / FINISHED).

    Inside a MachineFactory sandbox this is ``MEGADESK_FACTORY_REDIS_URL`` (live
    db 0). ``REDIS_URL`` is the agent's own MegaDesk pair and must not be used
    for that handshake. Outside a sandbox, falls back to ``REDIS_URL``.
    """
    if redis_url is not None:
        return resolve_redis_url(redis_url)
    factory = (os.environ.get(ENV_FACTORY_REDIS_URL) or "").strip()
    if factory:
        return factory
    return resolve_redis_url()


def redis_url_host_port(redis_url: str) -> tuple[str, int]:
    """Host and port from a Redis URL (defaults: localhost / 6379)."""
    parsed = urlparse(redis_url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 6379
    return host, port


def redis_url_db(redis_url: str) -> int:
    """Database index named by a Redis URL path (default 0)."""
    path = (urlparse(redis_url).path or "").lstrip("/")
    if not path:
        return 0
    first = path.split("/", 1)[0]
    try:
        return int(first)
    except ValueError:
        return 0


def redis_url_with_db(redis_url: str, db: int) -> str:
    """Return ``redis_url`` with the path rewritten to ``/{db}``."""
    parsed = urlparse(redis_url)
    return urlunparse(parsed._replace(path=f"/{int(db)}"))


def resolve_redis_pair(redis_url: Optional[str] = None) -> tuple[int, int]:
    """Ephemeral and persistent DB indexes for this process.

    Live URLs (db 0 or 1) stay ``(0, 1)``. Any other even index ``N`` is the
    pair ``(N, N+1)``; an odd index snaps to ``(N-1, N)``.
    """
    db = redis_url_db(resolve_redis_url(redis_url))
    if db in (REDIS_DB_EPHEMERAL, REDIS_DB_PERSISTENT):
        return (REDIS_DB_EPHEMERAL, REDIS_DB_PERSISTENT)
    if db % 2 == 1:
        return (db - 1, db)
    return (db, db + 1)


def resolve_ephemeral_db(redis_url: Optional[str] = None) -> int:
    return resolve_redis_pair(redis_url)[0]


def resolve_persistent_db(redis_url: Optional[str] = None) -> int:
    return resolve_redis_pair(redis_url)[1]


def redis_connect(
    redis_url: Optional[str] = None,
    *,
    db: int,
    decode_responses: bool = True,
    **kwargs: object,
):
    """``Redis.from_url`` with the path rewritten to ``db``.

    redis-py 8 ignores the ``db=`` keyword when the URL already names a
    database, so callers must not pass ``db=`` and a path together.
    """
    url = redis_url_with_db(resolve_redis_url(redis_url), db)
    return redis.Redis.from_url(url, decode_responses=decode_responses, **kwargs)


def dev_flush_mode_enabled() -> bool:
    """True when ``DEV_FLUSH_MODE`` is ``1`` / ``true`` / ``yes`` / ``on`` (case-insensitive)."""
    raw = (os.environ.get(ENV_DEV_FLUSH_MODE) or "").strip().lower()
    return raw in _DEV_FLUSH_MODE_TRUTHY


def flush_live_redis_pair(redis_url: Optional[str] = None) -> None:
    """FLUSHDB live DBs 0 then 1. The only allowed live-pair flush.

    Canvas boot calls this when ``DEV_FLUSH_MODE`` is on. Pytest,
    ``python -m supervisor`` alone, and agent sandboxes must not call this.
    """
    url = resolve_redis_url(redis_url)
    for db in (REDIS_DB_EPHEMERAL, REDIS_DB_PERSISTENT):
        client = redis_connect(url, db=db)
        try:
            client.flushdb()
        finally:
            client.close()


def running_nodes_key(unique_id: str) -> str:
    return f"{RUNNINGNODES_PREFIX}{unique_id}"


class SupervisorClient:
    def __init__(self, redis_url: Optional[str] = None) -> None:
        self.redis_url = resolve_redis_url(redis_url)
        ephemeral_db, persistent_db = resolve_redis_pair(self.redis_url)
        self.ephemeral = redis_connect(self.redis_url, db=ephemeral_db)
        self.persistent = redis_connect(self.redis_url, db=persistent_db)

    def redis_ok(self) -> bool:
        try:
            return bool(self.ephemeral.ping())
        except Exception:
            return False

    def backend_ok(self) -> bool:
        try:
            return self.persistent.exists(SUPERVISOR_ALIVE_KEY) == 1
        except Exception:
            return False

    def launch_node(
        self,
        node_endpoint: str,
        parameters: "str | Mapping[str, str]" = "",
    ) -> str:
        """Fire-and-forget XADD to LAUNCHREQUEST (db0). Returns stream entry id.

        ``parameters`` may be the graph values a node declared, in which case
        they cross the stream as a JSON object.
        """
        from megadesk_contracts.parameters import parameters_to_json

        payload = parameters if isinstance(parameters, str) else parameters_to_json(parameters)
        return self.ephemeral.xadd(
            LAUNCHREQUEST_STREAM,
            {
                "node_endpoint": node_endpoint,
                "parameters": payload,
            },
        )

    def kill_node(self, node_endpoint: str, unique_id: str) -> str:
        """Fire-and-forget XADD to KILLREQUEST (db0). Returns stream entry id."""
        return self.ephemeral.xadd(
            KILLREQUEST_STREAM,
            {
                "node_endpoint": node_endpoint,
                "unique_id": unique_id,
            },
        )

    def list_running(self) -> list[dict[str, str]]:
        """Running nodes: live NODEHB keys, plus grace-window RUNNINGNODES."""
        from megadesk_contracts.node_runtime import (
            NODE_HEARTBEAT_PREFIX,
            is_reported_node_alive,
        )

        by_uid: dict[str, dict[str, str]] = {}
        for key in self.persistent.scan_iter(match=f"{NODE_HEARTBEAT_PREFIX}*", count=100):
            hb = self.persistent.hgetall(key) or {}
            if not hb:
                continue
            uid = (hb.get("unique_id") or "").strip()
            if not uid:
                text = str(key)
                if text.startswith(NODE_HEARTBEAT_PREFIX):
                    uid = text[len(NODE_HEARTBEAT_PREFIX) :]
            if not uid:
                continue
            stored = self.persistent.hgetall(running_nodes_key(uid)) or {}
            entry = dict(stored)
            entry["unique_id"] = stored.get("unique_id") or uid
            node = (hb.get("node") or "").strip()
            if node:
                entry.setdefault("node_endpoint", node)
            if hb.get("pid"):
                entry["node_pid"] = hb["pid"]
                entry.setdefault("PID", hb["pid"])
            if hb.get("status"):
                entry.setdefault("status", hb["status"])
            by_uid[uid] = entry

        for key in self.persistent.scan_iter(match=f"{RUNNINGNODES_PREFIX}*", count=100):
            data = self.persistent.hgetall(key)
            if not data:
                continue
            uid = (data.get("unique_id") or "").strip()
            if not uid:
                text = str(key)
                if text.startswith(RUNNINGNODES_PREFIX):
                    uid = text[len(RUNNINGNODES_PREFIX) :]
            if not uid:
                continue
            if uid in by_uid:
                merged = dict(data)
                if by_uid[uid].get("node_pid"):
                    merged["node_pid"] = by_uid[uid]["node_pid"]
                by_uid[uid] = merged
                continue
            if is_reported_node_alive(data, self.persistent):
                by_uid[uid] = data

        out = list(by_uid.values())
        out.sort(key=lambda d: (d.get("node_endpoint", ""), d.get("unique_id", "")))
        return out

    def get_running(self, unique_id: str) -> Optional[dict[str, str]]:
        data = self.persistent.hgetall(running_nodes_key(unique_id))
        return data or None

    def kill_all_running(self) -> int:
        from megadesk_contracts.node_runtime import request_shutdown

        running = self.list_running()
        for entry in running:
            endpoint = entry.get("node_endpoint") or ""
            uid = entry.get("unique_id") or ""
            if uid:
                request_shutdown(self.persistent, uid)
            if endpoint and uid:
                self.kill_node(endpoint, uid)
        return len(running)


def _canvas_root() -> Path:
    """Locate the canvas that owns this process — never another worktree's install."""
    from megadesk_contracts.paths import resolve_canvas_root

    return resolve_canvas_root()


def ensure_supervisor_running(
    *,
    timeout: float = 12.0,
    redis_url: Optional[str] = None,
) -> bool:
    """Spawn the Canvas-owned Supervisor BE if it is not already alive.

    Canvas launches ``python -m supervisor`` on startup. Redis db1 holds the
    singleton flag so a second BE cannot start while one is alive.

    A new log session is created only when this function actually spawns a
    Supervisor. If one is already alive, the existing ``Logs/CURRENT`` session
    is reused — canvas open does not rotate logs.
    """
    from megadesk_contracts.log_session import (
        attach_log_session,
        begin_log_session,
        session_log_path,
        update_current_session,
    )
    from megadesk_contracts.paths import ENV_CANVAS_ROOT, ENV_LOGS_DIR, ENV_LOGS_ROOT

    client = SupervisorClient(redis_url=redis_url)
    if client.backend_ok():
        try:
            attach_log_session()
        except Exception:
            pass
        return True

    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]

    root = _canvas_root()
    session_dir = begin_log_session()
    log_path = session_log_path("supervisor")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_fh = open(log_path, "a", encoding="utf-8", errors="replace", buffering=1)
    env = os.environ.copy()
    env[ENV_CANVAS_ROOT] = str(root)
    env[ENV_LOGS_DIR] = str(session_dir)
    env[ENV_LOGS_ROOT] = str(session_dir.parent)

    try:
        from datetime import datetime, timezone

        started = datetime.now(timezone.utc).isoformat()
        log_fh.write(f"--- supervisor spawn at {started} canvas={root} session={session_dir} ---\n")
        log_fh.flush()
        proc = subprocess.Popen(
            [sys.executable, "-u", "-m", "supervisor"],
            cwd=str(root),
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=creationflags,
            env=env,
        )
        try:
            update_current_session(supervisor_pid=proc.pid)
        except Exception:
            pass
        log_fh.write(f"--- supervisor pid={proc.pid} ---\n")
        log_fh.flush()
    except Exception:
        return False
    finally:
        # Child inherits the fd; close our copy so we do not leak handles.
        try:
            log_fh.close()
        except Exception:
            pass

    deadline = time.time() + timeout
    while time.time() < deadline:
        if client.backend_ok():
            return True
        time.sleep(0.25)
    return client.backend_ok()
