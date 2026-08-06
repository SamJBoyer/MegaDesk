"""Thin Redis stream client for Supervisor LAUNCHREQUEST / KILLREQUEST."""

from __future__ import annotations

import subprocess
import sys
import time
from typing import Optional

try:
    import redis
except ImportError:  # pragma: no cover
    redis = None  # type: ignore


REDIS_HOST = "localhost"
REDIS_PORT = 6379
SUPERVISOR_ALIVE_KEY = "GBD:SUPERVISOR:ALIVE"
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
        self.client = redis.Redis(host=host, port=port, db=0, decode_responses=True)

    def redis_ok(self) -> bool:
        try:
            return bool(self.client.ping())
        except Exception:
            return False

    def backend_ok(self) -> bool:
        try:
            return self.client.exists(SUPERVISOR_ALIVE_KEY) == 1
        except Exception:
            return False

    def launch_node(self, node_endpoint: str, parameters: str = "") -> str:
        """Fire-and-forget XADD to LAUNCHREQUEST. Returns stream entry id."""
        return self.client.xadd(
            LAUNCHREQUEST_STREAM,
            {
                "node_endpoint": node_endpoint,
                "parameters": parameters,
            },
        )

    def kill_node(self, node_endpoint: str, unique_id: str) -> str:
        """Fire-and-forget XADD to KILLREQUEST. Returns stream entry id."""
        return self.client.xadd(
            KILLREQUEST_STREAM,
            {
                "node_endpoint": node_endpoint,
                "unique_id": unique_id,
            },
        )

    def list_running(self) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for key in self.client.scan_iter(match=f"{RUNNINGNODES_PREFIX}*", count=100):
            data = self.client.hgetall(key)
            if data:
                out.append(data)
        out.sort(key=lambda d: (d.get("node_endpoint", ""), d.get("unique_id", "")))
        return out

    def get_running(self, unique_id: str) -> Optional[dict[str, str]]:
        data = self.client.hgetall(running_nodes_key(unique_id))
        return data or None

    def kill_all_running(self) -> int:
        running = self.list_running()
        for entry in running:
            endpoint = entry.get("node_endpoint") or ""
            uid = entry.get("unique_id") or ""
            if endpoint and uid:
                self.kill_node(endpoint, uid)
        return len(running)


def ensure_supervisor_running(
    *,
    timeout: float = 12.0,
    host: str = REDIS_HOST,
    port: int = REDIS_PORT,
) -> bool:
    """Spawn the Supervisor BeSpec if the BE is not already alive.

    Used when the Supervisor FE is dropped on the canvas (chicken-and-egg:
    ``LAUNCHREQUEST`` requires the Supervisor BE, so the BeSpec is started
    directly from its ``MegaDesk.nodes`` BE spec).
    """
    if redis is None:
        return False

    client = SupervisorClient(host=host, port=port)
    if client.backend_ok():
        return True

    from megadesk.discovery import get_backend

    spec = get_backend(SUPERVISOR_NODE_NAME)
    if spec is None:
        return False

    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]

    subprocess.Popen(
        list(spec.argv),
        cwd=spec.cwd or None,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        creationflags=creationflags,
    )

    deadline = time.time() + timeout
    while time.time() < deadline:
        if client.backend_ok():
            return True
        time.sleep(0.25)
    return client.backend_ok()
