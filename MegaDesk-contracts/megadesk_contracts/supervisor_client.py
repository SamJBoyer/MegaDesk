"""Thin Redis stream client for Supervisor LAUNCHREQUEST / KILLREQUEST.

Redis databases:
  db0 — ephemeral streams (LAUNCHREQUEST / KILLREQUEST / NODEEXIT, MissionControl traffic)
  db1 — persistent supervisor state (singleton, RUNNINGNODES, alive heartbeat)
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

try:
    import redis
except ImportError:  # pragma: no cover
    redis = None  # type: ignore


REDIS_HOST = "localhost"
REDIS_PORT = 6379
REDIS_DB_EPHEMERAL = 0
REDIS_DB_PERSISTENT = 1
SUPERVISOR_ALIVE_KEY = "GBD:SUPERVISOR:ALIVE"
SUPERVISOR_SINGLETON_KEY = "GBD:SUPERVISOR:SINGLETON"
SUPERVISOR_NODE_NAME = "supervisor"
LAUNCHREQUEST_STREAM = "LAUNCHREQUEST"
KILLREQUEST_STREAM = "KILLREQUEST"
RUNNINGNODES_PREFIX = "RUNNINGNODES:"


def running_nodes_key(unique_id: str) -> str:
    return f"{RUNNINGNODES_PREFIX}{unique_id}"


class SupervisorClient:
    def __init__(
        self,
        host: str = REDIS_HOST,
        port: int = REDIS_PORT,
        **_ignored: object,
    ) -> None:
        if redis is None:
            raise RuntimeError("redis package is required for SupervisorClient")
        # caller_identity kept as ignored kwarg for call-site compatibility.
        self.ephemeral = redis.Redis(
            host=host, port=port, db=REDIS_DB_EPHEMERAL, decode_responses=True
        )
        self.persistent = redis.Redis(
            host=host, port=port, db=REDIS_DB_PERSISTENT, decode_responses=True
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
        out: list[dict[str, str]] = []
        for key in self.persistent.scan_iter(match=f"{RUNNINGNODES_PREFIX}*", count=100):
            data = self.persistent.hgetall(key)
            if data:
                out.append(data)
        out.sort(key=lambda d: (d.get("node_endpoint", ""), d.get("unique_id", "")))
        return out

    def get_running(self, unique_id: str) -> Optional[dict[str, str]]:
        data = self.persistent.hgetall(running_nodes_key(unique_id))
        return data or None

    def kill_all_running(self) -> int:
        running = self.list_running()
        for entry in running:
            endpoint = entry.get("node_endpoint") or ""
            uid = entry.get("unique_id") or ""
            if endpoint and uid:
                self.kill_node(endpoint, uid)
        return len(running)


def _canvas_root() -> Path:
    """Locate MegaDesk-Canvas root (cwd when running main.py, or installed package)."""
    here = Path(__file__).resolve()
    # megadesk_contracts lives beside MegaDesk-Canvas in the monorepo.
    sibling = here.parents[2] / "MegaDesk-Canvas"
    if (sibling / "supervisor").is_dir():
        return sibling
    # Fallback: current working directory when launched via ``python main.py``.
    cwd = Path.cwd()
    if (cwd / "supervisor").is_dir():
        return cwd
    if cwd.name == "MegaDesk-Canvas":
        return cwd
    return sibling


def ensure_supervisor_running(
    *,
    timeout: float = 12.0,
    host: str = REDIS_HOST,
    port: int = REDIS_PORT,
) -> bool:
    """Spawn the Canvas-owned Supervisor BE if it is not already alive.

    Canvas launches ``python -m supervisor`` on startup. Redis db1 holds the
    singleton flag so a second BE cannot start while one is alive.
    """
    if redis is None:
        return False

    client = SupervisorClient(host=host, port=port)
    if client.backend_ok():
        return True

    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]

    root = _canvas_root()
    log_path = (root / "logs" / "supervisor" / "supervisor.log").resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_fh = open(log_path, "a", encoding="utf-8", errors="replace", buffering=1)

    try:
        subprocess.Popen(
            [sys.executable, "-u", "-m", "supervisor"],
            cwd=str(root),
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=creationflags,
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
