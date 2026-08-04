"""Thin Redis Pub/Sub client for Supervisor launch_node / stop_node."""

from __future__ import annotations

import time
import uuid
from typing import Optional

try:
    import redis
except ImportError:  # pragma: no cover
    redis = None  # type: ignore


REDIS_HOST = "localhost"
REDIS_PORT = 6379
COMMANDER_ALIVE_KEY = "GBD:COMMANDER:ALIVE"


class SupervisorClient:
    def __init__(
        self,
        caller_identity: Optional[str] = None,
        host: str = REDIS_HOST,
        port: int = REDIS_PORT,
        timeout: float = 5.0,
    ) -> None:
        if redis is None:
            raise RuntimeError("redis package is required for SupervisorClient")
        self.identity = caller_identity or f"executive-{uuid.uuid4().hex[:8]}"
        self.timeout = timeout
        self.client = redis.Redis(host=host, port=port, db=0, decode_responses=True)
        self.ack_channel = f"acknowledgements:{self.identity}"

    def redis_ok(self) -> bool:
        try:
            return bool(self.client.ping())
        except Exception:
            return False

    def backend_ok(self) -> bool:
        try:
            return self.client.exists(COMMANDER_ALIVE_KEY) == 1
        except Exception:
            return False

    def request(self, channel_prefix: str, body: str) -> Optional[str]:
        pubsub = self.client.pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe(self.ack_channel)
        time.sleep(0.05)
        try:
            self.client.publish(f"{channel_prefix}:{self.identity}", body)
            deadline = time.time() + self.timeout
            while time.time() < deadline:
                message = pubsub.get_message(timeout=0.25)
                if message is None:
                    continue
                if message.get("type") != "message":
                    continue
                data = message.get("data")
                if isinstance(data, bytes):
                    data = data.decode("utf-8", errors="replace")
                return str(data)
            return None
        finally:
            pubsub.close()

    def launch_node(self, name: str) -> Optional[str]:
        return self.request("launch_node", name)

    def stop_node(self, name: str) -> Optional[str]:
        return self.request("stop_node", name)
