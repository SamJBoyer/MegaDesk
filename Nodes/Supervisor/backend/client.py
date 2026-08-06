"""Fire-and-forget Redis stream client for Supervisor FE and smoke tests."""

from __future__ import annotations

from typing import Optional

import redis

from backend.redis_provision import (
    KILLREQUEST_STREAM,
    LAUNCHREQUEST_STREAM,
    REDIS_HOST,
    REDIS_PORT,
    RUNNINGNODES_PREFIX,
    is_supervisor_alive,
    ping_redis,
    running_nodes_key,
)


class SupervisorStreamClient:
    def __init__(
        self,
        host: str = REDIS_HOST,
        port: int = REDIS_PORT,
    ) -> None:
        self.client = redis.Redis(host=host, port=port, db=0, decode_responses=True)

    def redis_ok(self) -> bool:
        return ping_redis()

    def backend_ok(self) -> bool:
        return is_supervisor_alive(self.client)

    def launch_node(self, node_endpoint: str, parameters: str = "") -> str:
        """XADD LAUNCHREQUEST. Returns the stream entry id (not unique_id)."""
        return self.client.xadd(
            LAUNCHREQUEST_STREAM,
            {
                "node_endpoint": node_endpoint,
                "parameters": parameters,
            },
        )

    def kill_node(self, node_endpoint: str, unique_id: str) -> str:
        """XADD KILLREQUEST. Returns the stream entry id."""
        return self.client.xadd(
            KILLREQUEST_STREAM,
            {
                "node_endpoint": node_endpoint,
                "unique_id": unique_id,
            },
        )

    def list_running(self) -> list[dict[str, str]]:
        """SCAN RUNNINGNODES:* and return hash field dicts."""
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
        """XADD a KILLREQUEST for every RUNNINGNODES hash. Returns count queued."""
        running = self.list_running()
        for entry in running:
            endpoint = entry.get("node_endpoint") or ""
            uid = entry.get("unique_id") or ""
            if endpoint and uid:
                self.kill_node(endpoint, uid)
        return len(running)
