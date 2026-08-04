"""Pub/Sub request helpers shared by the MVP frontend and smoke tests."""

from __future__ import annotations

import time
import uuid
from typing import Optional

import redis

from commander.redis_provision import REDIS_HOST, REDIS_PORT, is_commander_alive, ping_redis


class PubSubClient:
    def __init__(
        self,
        caller_identity: Optional[str] = None,
        host: str = REDIS_HOST,
        port: int = REDIS_PORT,
        timeout: float = 5.0,
    ) -> None:
        self.identity = caller_identity or f"frontend-{uuid.uuid4().hex[:8]}"
        self.timeout = timeout
        self.client = redis.Redis(host=host, port=port, db=0, decode_responses=True)
        self.ack_channel = f"acknowledgements:{self.identity}"

    def redis_ok(self) -> bool:
        return ping_redis()

    def backend_ok(self) -> bool:
        return is_commander_alive(self.client)

    def request(self, channel_prefix: str, body: str) -> Optional[str]:
        """Publish to <prefix>:<identity> and wait for one ack message."""
        pubsub = self.client.pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe(self.ack_channel)
        # Allow subscription to settle
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

    def register(self, path: str) -> Optional[str]:
        return self.request("register_manifest", path)

    def validate(self, path: str) -> Optional[str]:
        return self.request("validate_manifest", path)

    def execute(self, guid: str) -> Optional[str]:
        return self.request("execute_manifest", guid)

    def killall(self) -> None:
        self.client.publish("KILLALL", "1")
