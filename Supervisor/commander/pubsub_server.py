"""Pub/Sub event surface for register, validate, execute, and KILLALL."""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import redis

from commander.engine import ExecutionEngine
from commander.redis_provision import (
    ALIVE_TTL_SECONDS,
    clear_commander_alive,
    mark_commander_alive,
)

log = logging.getLogger("gbd.commander")

REGISTER_PATTERN = "register_manifest:*"
EXECUTE_PATTERN = "execute_manifest:*"
VALIDATE_PATTERN = "validate_manifest:*"
KILLALL_CHANNEL = "KILLALL"


def _identity_from_channel(channel: str, prefix: str) -> Optional[str]:
    # channel like register_manifest:<identity>
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
        pubsub.psubscribe(REGISTER_PATTERN, EXECUTE_PATTERN, VALIDATE_PATTERN)
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

        if channel.startswith("register_manifest:"):
            identity = _identity_from_channel(channel, "register_manifest")
            if not identity:
                return
            self._on_register(identity, data.strip())
        elif channel.startswith("execute_manifest:"):
            identity = _identity_from_channel(channel, "execute_manifest")
            if not identity:
                return
            self._on_execute(identity, data.strip())
        elif channel.startswith("validate_manifest:"):
            identity = _identity_from_channel(channel, "validate_manifest")
            if not identity:
                return
            self._on_validate(identity, data.strip())

    def _on_register(self, identity: str, path: str) -> None:
        try:
            guid = self.engine.register(path)
            _ack(self.realtime, identity, f"SUCCESS {guid}")
            log.info("Registered %s -> %s", path, guid)
        except Exception as exc:
            log.warning("Register failed for %s: %s", path, exc)
            _ack(self.realtime, identity, "FAILED")

    def _on_validate(self, identity: str, path: str) -> None:
        try:
            self.engine.validate_path(path)
            _ack(self.realtime, identity, "SUCCESS")
            log.info("Validated OK: %s", path)
        except Exception as exc:
            log.warning("Validate failed for %s: %s", path, exc)
            _ack(self.realtime, identity, "FAILED")

    def _on_execute(self, identity: str, guid: str) -> None:
        try:
            self.engine.execute(guid)
            _ack(self.realtime, identity, "SUCCESS")
            log.info("Executed GUID %s", guid)
        except Exception as exc:
            log.warning("Execute failed for %s: %s", guid, exc)
            _ack(self.realtime, identity, "FAILED")
