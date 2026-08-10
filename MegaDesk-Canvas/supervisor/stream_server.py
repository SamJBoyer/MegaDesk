"""Stream consumer for LAUNCHREQUEST and KILLREQUEST (db0)."""

from __future__ import annotations

import logging
import os
import threading
from typing import Optional

import redis

from supervisor.engine import ExecutionEngine, NodeKillError, NodeLaunchError
from supervisor.redis_provision import (
    ALIVE_TTL_SECONDS,
    CONSUMER_GROUP,
    KILLREQUEST_STREAM,
    LAUNCHREQUEST_STREAM,
    clear_supervisor_alive,
    ensure_consumer_groups,
    mark_supervisor_alive,
    refresh_supervisor_singleton,
    release_supervisor_singleton,
)

log = logging.getLogger("gbd.supervisor")

_REAP_INTERVAL_S = 1.0


class SupervisorServer:
    def __init__(
        self,
        ephemeral: redis.Redis,
        persistent: redis.Redis,
        engine: ExecutionEngine,
        *,
        consumer_name: str = "supervisor-be",
        singleton_owner: Optional[str] = None,
    ) -> None:
        self.ephemeral = ephemeral
        self.persistent = persistent
        self.engine = engine
        self.consumer_name = consumer_name
        self.singleton_owner = singleton_owner or str(os.getpid())
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._heartbeat: Optional[threading.Thread] = None
        self._reaper: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        ensure_consumer_groups(self.ephemeral)
        mark_supervisor_alive(self.persistent)
        self._heartbeat = threading.Thread(
            target=self._heartbeat_loop, name="gbd-supervisor-heartbeat", daemon=True
        )
        self._reaper = threading.Thread(
            target=self._reaper_loop, name="gbd-supervisor-reaper", daemon=True
        )
        self._thread = threading.Thread(
            target=self._listen_loop, name="gbd-supervisor-streams", daemon=True
        )
        self._heartbeat.start()
        self._reaper.start()
        self._thread.start()
        log.info("Supervisor stream consumer listening")

    def stop(self) -> None:
        self._stop.set()
        clear_supervisor_alive(self.persistent)
        release_supervisor_singleton(self.persistent, owner=self.singleton_owner)
        if self._thread:
            self._thread.join(timeout=3)
        if self._heartbeat:
            self._heartbeat.join(timeout=3)
        if self._reaper:
            self._reaper.join(timeout=3)

    def _heartbeat_loop(self) -> None:
        while not self._stop.is_set():
            try:
                mark_supervisor_alive(self.persistent, ttl=ALIVE_TTL_SECONDS)
                if not refresh_supervisor_singleton(
                    self.persistent, owner=self.singleton_owner
                ):
                    log.error("Lost supervisor singleton — shutting down")
                    self._stop.set()
                    break
            except Exception as exc:
                log.warning("Heartbeat failed: %s", exc)
            self._stop.wait(ALIVE_TTL_SECONDS / 2)

    def _reaper_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.engine.reap_exits()
            except Exception as exc:
                log.warning("Reaper failed: %s", exc)
            self._stop.wait(_REAP_INTERVAL_S)

    def _listen_loop(self) -> None:
        streams = {
            LAUNCHREQUEST_STREAM: ">",
            KILLREQUEST_STREAM: ">",
        }
        while not self._stop.is_set():
            try:
                results = self.ephemeral.xreadgroup(
                    CONSUMER_GROUP,
                    self.consumer_name,
                    streams,
                    count=10,
                    block=500,
                )
            except redis.ResponseError as exc:
                if "NOGROUP" in str(exc):
                    ensure_consumer_groups(self.ephemeral)
                    continue
                log.exception("xreadgroup failed: %s", exc)
                self._stop.wait(0.5)
                continue
            except Exception as exc:
                log.exception("Stream read error: %s", exc)
                self._stop.wait(0.5)
                continue

            if not results:
                continue

            for stream_name, messages in results:
                for msg_id, fields in messages:
                    try:
                        self._handle_message(stream_name, fields)
                    except Exception:
                        log.exception(
                            "Error handling %s message %s: %s",
                            stream_name,
                            msg_id,
                            fields,
                        )
                    try:
                        self.ephemeral.xack(stream_name, CONSUMER_GROUP, msg_id)
                    except Exception as exc:
                        log.warning("xack failed for %s %s: %s", stream_name, msg_id, exc)

    def _handle_message(self, stream_name: str, fields: dict) -> None:
        if stream_name == LAUNCHREQUEST_STREAM:
            node_endpoint = str(fields.get("node_endpoint") or "").strip()
            parameters = str(fields.get("parameters") or "")
            try:
                unique_id = self.engine.launch(node_endpoint, parameters=parameters)
                log.info("LAUNCHREQUEST %s -> unique_id=%s", node_endpoint, unique_id)
            except NodeLaunchError as exc:
                log.warning("LAUNCHREQUEST failed: %s", exc)
            return

        if stream_name == KILLREQUEST_STREAM:
            node_endpoint = str(fields.get("node_endpoint") or "").strip()
            unique_id = str(fields.get("unique_id") or "").strip()
            try:
                self.engine.kill(node_endpoint, unique_id)
                log.info("KILLREQUEST %s unique_id=%s OK", node_endpoint, unique_id)
            except NodeKillError as exc:
                log.warning("KILLREQUEST failed: %s", exc)
            return
