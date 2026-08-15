"""Thin Redis stream client for Supervisor LAUNCHREQUEST / KILLREQUEST.

Redis databases:
  db0 — ephemeral streams (LAUNCHREQUEST / KILLREQUEST / NODEEXIT, MissionControl traffic)
  db1 — persistent supervisor state (singleton, RUNNINGNODES, alive heartbeat)

Connection standard: ``REDIS_URL`` (default ``redis://localhost:6379/0``).
Ephemeral vs persistent is selected via the ``db`` argument on ``Redis.from_url``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

try:
    import redis
except ImportError:  # pragma: no cover
    redis = None  # type: ignore


DEFAULT_REDIS_URL = "redis://localhost:6379/0"
REDIS_DB_EPHEMERAL = 0
REDIS_DB_PERSISTENT = 1
SUPERVISOR_ALIVE_KEY = "GBD:SUPERVISOR:ALIVE"
SUPERVISOR_SINGLETON_KEY = "GBD:SUPERVISOR:SINGLETON"
SUPERVISOR_NODE_NAME = "supervisor"
LAUNCHREQUEST_STREAM = "LAUNCHREQUEST"
KILLREQUEST_STREAM = "KILLREQUEST"
RUNNINGNODES_PREFIX = "RUNNINGNODES:"


def resolve_redis_url(redis_url: Optional[str] = None) -> str:
    """Resolve the Redis URL: explicit arg, else ``REDIS_URL``, else default."""
    raw = redis_url if redis_url is not None else os.environ.get("REDIS_URL")
    text = (raw or DEFAULT_REDIS_URL).strip()
    return text or DEFAULT_REDIS_URL


def redis_url_host_port(redis_url: str) -> tuple[str, int]:
    """Host and port from a Redis URL (defaults: localhost / 6379)."""
    parsed = urlparse(redis_url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 6379
    return host, port


def running_nodes_key(unique_id: str) -> str:
    return f"{RUNNINGNODES_PREFIX}{unique_id}"


class SupervisorClient:
    def __init__(
        self,
        redis_url: Optional[str] = None,
        **_ignored: object,
    ) -> None:
        if redis is None:
            raise RuntimeError("redis package is required for SupervisorClient")
        # caller_identity / legacy host+port kept as ignored kwargs for call-site compat.
        self.redis_url = resolve_redis_url(redis_url)
        self.ephemeral = redis.Redis.from_url(
            self.redis_url, db=REDIS_DB_EPHEMERAL, decode_responses=True
        )
        self.persistent = redis.Redis.from_url(
            self.redis_url, db=REDIS_DB_PERSISTENT, decode_responses=True
        )
        # Back-compat alias: older call sites used ``.client`` for streams.
        self.client = self.ephemeral

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

    def launch_node(self, node_endpoint: str, parameters: str = "") -> str:
        """Fire-and-forget XADD to LAUNCHREQUEST (db0). Returns stream entry id."""
        return self.ephemeral.xadd(
            LAUNCHREQUEST_STREAM,
            {
                "node_endpoint": node_endpoint,
                "parameters": parameters,
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
        """Alive instances only — dead PIDs and stale hashes are omitted."""
        from megadesk_contracts.node_runtime import heartbeat_key, is_reported_node_alive

        out: list[dict[str, str]] = []
        for key in self.persistent.scan_iter(match=f"{RUNNINGNODES_PREFIX}*", count=100):
            data = self.persistent.hgetall(key)
            if not data or not is_reported_node_alive(data, self.persistent):
                continue
            uid = (data.get("unique_id") or "").strip()
            if uid:
                hb = self.persistent.hgetall(heartbeat_key(uid)) or {}
                if hb.get("pid"):
                    data["node_pid"] = hb["pid"]
            out.append(data)
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
    """
    if redis is None:
        return False

    client = SupervisorClient(redis_url=redis_url)
    if client.backend_ok():
        return True

    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]

    from megadesk_contracts.paths import ENV_CANVAS_ROOT

    root = _canvas_root()
    log_path = (root / "logs" / "supervisor" / "supervisor.log").resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_fh = open(log_path, "a", encoding="utf-8", errors="replace", buffering=1)
    env = os.environ.copy()
    env[ENV_CANVAS_ROOT] = str(root)

    try:
        subprocess.Popen(
            [sys.executable, "-u", "-m", "supervisor"],
            cwd=str(root),
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=creationflags,
            env=env,
        )
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
