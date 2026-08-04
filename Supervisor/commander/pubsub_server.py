"""Pub/Sub event surface for launch_node, stop_node, and KILLALL."""

from __future__ import annotations

import logging
import threading
from typing import Optional

import redis

from commander.engine import ExecutionEngine
from commander.redis_provision import (
    ALIVE_TTL_SECONDS,
    clear_commander_alive,
    mark_commander_alive,
)

log = logging.getLogger("gbd.commander")

LAUNCH_PATTERN = "launch_node:*"
STOP_PATTERN = "stop_node:*"
KILLALL_CHANNEL = "KILLALL"


def _identity_from_channel(channel: str, prefix: str) -> Optional[str]:
    parts = channel.split(":", 1)
    if len(parts) != 2 or parts[0] != prefix:
        return None
    identity = parts[1].strip()
    return identity or None


def _ack(pubsub_client: redis.Redis, identity: str, message: str) -> None:
    pubsub_client.publish(f"acknowledgements:{identity}", message)


class CommanderServer:
    def __init__(self, realtime: redis.Redis, engine: ExecutionEngine) -> None:
        self.realtime = realtime
        self.engine = engine
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._heartbeat: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        mark_commander_alive(self.realtime)
        self._heartbeat = threading.Thread(target=self._heartbeat_loop, name="gbd-heartbeat", daemon=True)
        self._thread = threading.Thread(target=self._listen_loop, name="gbd-pubsub", daemon=True)
        self._heartbeat.start()
        self._thread.start()
        log.info("Commander Pub/Sub listening")

    def stop(self) -> None:
        self._stop.set()
        clear_commander_alive(self.realtime)
        if self._thread:
            self._thread.join(timeout=3)
        if self._heartbeat:
            self._heartbeat.join(timeout=3)

    def _heartbeat_loop(self) -> None:
        while not self._stop.is_set():
            try:
                mark_commander_alive(self.realtime, ttl=ALIVE_TTL_SECONDS)
            except Exception as exc:
                log.warning("Heartbeat failed: %s", exc)
            self._stop.wait(ALIVE_TTL_SECONDS / 2)

    def _listen_loop(self) -> None:
        pubsub = self.realtime.pubsub(ignore_subscribe_messages=True)
        pubsub.psubscribe(LAUNCH_PATTERN, STOP_PATTERN)
        pubsub.subscribe(KILLALL_CHANNEL)
        try:
            while not self._stop.is_set():
                message = pubsub.get_message(timeout=0.5)
                if message is None:
                    continue
                try:
                    self._handle_message(message)
                except Exception:
                    log.exception("Error handling Pub/Sub message: %s", message)
        finally:
            try:
                pubsub.close()
            except Exception:
                pass

    def _handle_message(self, message: dict) -> None:
        msg_type = message.get("type")
        data = message.get("data")
        if isinstance(data, bytes):
            data = data.decode("utf-8", errors="replace")
        if not isinstance(data, str):
            data = str(data) if data is not None else ""

        if msg_type == "message":
            channel = message.get("channel")
            if isinstance(channel, bytes):
                channel = channel.decode("utf-8", errors="replace")
            if channel == KILLALL_CHANNEL:
                log.info("KILLALL received")
                self.engine.kill_all()
            return

        if msg_type != "pmessage":
            return

        channel = message.get("channel")
        if isinstance(channel, bytes):
            channel = channel.decode("utf-8", errors="replace")
        if not isinstance(channel, str):
            return

        if channel.startswith("launch_node:"):
            identity = _identity_from_channel(channel, "launch_node")
            if not identity:
                return
            self._on_launch(identity, data.strip())
        elif channel.startswith("stop_node:"):
            identity = _identity_from_channel(channel, "stop_node")
            if not identity:
                return
            self._on_stop(identity, data.strip())

    def _on_launch(self, identity: str, name: str) -> None:
        try:
            self.engine.launch(name)
            _ack(self.realtime, identity, "SUCCESS")
            log.info("launch_node %s OK", name)
        except Exception as exc:
            log.warning("launch_node failed for %s: %s", name, exc)
            _ack(self.realtime, identity, "FAILED")

    def _on_stop(self, identity: str, name: str) -> None:
        try:
            self.engine.stop(name)
            _ack(self.realtime, identity, "SUCCESS")
            log.info("stop_node %s OK", name)
        except Exception as exc:
            log.warning("stop_node failed for %s: %s", name, exc)
            _ack(self.realtime, identity, "FAILED")
