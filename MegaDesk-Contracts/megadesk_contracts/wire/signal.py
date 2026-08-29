"""Pub/sub execution signals for factory orders.

Streams store a reference of what was ordered. They do not start work.
A PUBLISH on the matching channel is the only execution signal: if nobody
is subscribed, the order is gone, which is what stops stale tickets from
re-running after a restart.
"""

from __future__ import annotations

import json
import time
from typing import Any, Mapping


def encode_fields(fields: Mapping[str, Any]) -> str:
    """Serialize a field map for PUBLISH. Values stay strings, as on the stream."""
    return json.dumps(
        {str(k): "" if v is None else str(v) for k, v in fields.items()},
        separators=(",", ":"),
    )


def decode_fields(payload: Any) -> dict[str, str]:
    """Parse a PUBLISH payload back into a string field map."""
    if isinstance(payload, bytes):
        payload = payload.decode()
    if not isinstance(payload, str):
        raise ValueError(
            f"order signal must be a string, got {type(payload).__name__}"
        )
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("order signal must be a JSON object")
    return {str(k): "" if v is None else str(v) for k, v in data.items()}


def publish_fields(redis: Any, channel: str, fields: Mapping[str, Any]) -> int:
    return int(redis.publish(channel, encode_fields(fields)))


class FieldInbox:
    """Subscribe to a channel and drain published field maps."""

    def __init__(self, redis: Any, channel: str) -> None:
        self._redis = redis
        self.channel = channel
        self._pubsub: Any = None

    def listen(self) -> None:
        if self._pubsub is not None:
            return
        pubsub = self._redis.pubsub()
        pubsub.subscribe(self.channel)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            msg = pubsub.get_message(timeout=0.1)
            if msg is None:
                continue
            if msg.get("type") == "subscribe":
                self._pubsub = pubsub
                return
        pubsub.close()
        raise TimeoutError(f"subscribe to {self.channel!r} did not confirm")

    def drain(self, *, timeout: float = 0.0) -> list[dict[str, str]]:
        if self._pubsub is None:
            self.listen()
        out: list[dict[str, str]] = []
        while True:
            msg = self._pubsub.get_message(timeout=timeout)
            if msg is None:
                break
            if msg.get("type") != "message":
                continue
            out.append(decode_fields(msg["data"]))
            timeout = 0.0
        return out

    def close(self) -> None:
        pubsub = self._pubsub
        self._pubsub = None
        if pubsub is None:
            return
        closer = getattr(pubsub, "close", None)
        if callable(closer):
            closer()
