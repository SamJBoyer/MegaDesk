"""Execution engine: discover BE nodes, launch/kill by unique_id, write RUNNINGNODES."""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Optional

import redis

from megadesk_contracts import BeSpec, discover_backends, get_backend
from megadesk_contracts.node_runtime import (
    is_reported_node_alive,
    request_shutdown,
)
from megadesk_contracts.supervisor_client import NODEEXIT_STREAM, running_nodes_key

from supervisor.process_registry import ProcessRegistry, launch_spec

log = logging.getLogger("megadesk.supervisor")


class NodeLaunchError(Exception):
    """Unknown node endpoint or launch failure."""


class NodeKillError(Exception):
    """Unknown unique_id or endpoint mismatch."""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ExecutionEngine:
    def __init__(
        self,
        ephemeral: redis.Redis,
        persistent: redis.Redis,
        registry: Optional[ProcessRegistry] = None,
    ) -> None:
        self.ephemeral = ephemeral
        self.persistent = persistent
        self.registry = registry or ProcessRegistry()
        self._backends: dict[str, BeSpec] = {}
        self._lock = threading.RLock()
        self.discover_backends()
        try:
            n = self.reconcile_stale()
            if n:
                log.info("Cleared %d stale RUNNINGNODES hash(es) at start", n)
        except Exception:
            log.exception("Stale RUNNINGNODES sweep failed at start")

    def discover_backends(self) -> dict[str, BeSpec]:
        """Refresh the in-memory map of name → BeSpec from megadesk_contracts."""
        with self._lock:
            self._backends = dict(discover_backends())
            log.info(
                "Discovered %d BE node(s): %s",
                len(self._backends),
                ", ".join(sorted(self._backends)) or "(none)",
            )
            return dict(self._backends)

    def list_backends(self) -> list[str]:
        with self._lock:
            return sorted(self._backends)

    def _resolve_spec(self, node_endpoint: str) -> BeSpec:
        with self._lock:
            spec = self._backends.get(node_endpoint)
        if spec is None:
            self.discover_backends()
            with self._lock:
                spec = self._backends.get(node_endpoint)
        if spec is None:
            spec = get_backend(node_endpoint)
            if spec is not None:
                with self._lock:
                    self._backends[spec.name] = spec
        if spec is None:
            raise NodeLaunchError(f"Unknown BE node: {node_endpoint}")
        return spec

    def launch(self, node_endpoint: str, parameters: str = "") -> str:
        """Launch a BE process; returns the assigned unique_id."""
        node_endpoint = node_endpoint.strip()
        if not node_endpoint:
            raise NodeLaunchError("Empty node_endpoint")

        spec = self._resolve_spec(node_endpoint)
        unique_id = str(uuid.uuid4())
        launched_at = _utc_now_iso()
        try:
            entry = launch_spec(spec, unique_id=unique_id, parameters=parameters)
        except Exception as exc:
            raise NodeLaunchError(f"Failed to launch {node_endpoint}: {exc}") from exc

        self.registry.store(entry)
        self.persistent.hset(
            running_nodes_key(unique_id),
            mapping={
                "node_endpoint": entry.node_endpoint,
                "unique_id": unique_id,
                "parameters": parameters,
                "PID": str(entry.pid),
                "status": "running",
                "log_path": entry.log_path,
                "launched_at": launched_at,
                "exit_code": "",
                "exited_at": "",
            },
        )
        log.info(
            "Launched %s unique_id=%s pid=%s log=%s argv=%s",
            entry.node_endpoint,
            unique_id,
            entry.pid,
            entry.log_path,
            spec.argv,
        )
        return unique_id

    def kill(self, node_endpoint: str, unique_id: str) -> None:
        node_endpoint = node_endpoint.strip()
        unique_id = unique_id.strip()
        if not unique_id:
            raise NodeKillError("Empty unique_id")

        entry = self.registry.get(unique_id)
        if entry is None:
            # Stale / exited hash without in-memory process — still clear Redis.
            key = running_nodes_key(unique_id)
            stored = self.persistent.hgetall(key)
            if not stored:
                raise NodeKillError(f"No managed process for unique_id: {unique_id}")
            if node_endpoint and stored.get("node_endpoint") != node_endpoint:
                raise NodeKillError(
                    f"node_endpoint mismatch for {unique_id}: "
                    f"expected {stored.get('node_endpoint')!r}, got {node_endpoint!r}"
                )
            self.persistent.delete(key)
            log.info("Cleared stale RUNNINGNODES for unique_id=%s", unique_id)
            return

        if node_endpoint and entry.node_endpoint != node_endpoint:
            raise NodeKillError(
                f"node_endpoint mismatch for {unique_id}: "
                f"expected {entry.node_endpoint!r}, got {node_endpoint!r}"
            )

        request_shutdown(self.persistent, unique_id)
        self.registry.stop(unique_id)
        self.persistent.delete(running_nodes_key(unique_id))
        log.info("Killed %s unique_id=%s", entry.node_endpoint, unique_id)

    def kill_all(self) -> None:
        ids = self.registry.all_ids()
        self.registry.kill_all()
        for unique_id in ids:
            try:
                self.persistent.delete(running_nodes_key(unique_id))
            except Exception:
                pass
        # Also clear any exited Redis-only hashes left behind by the reaper.
        try:
            for key in self.persistent.scan_iter(match="RUNNINGNODES:*", count=100):
                self.persistent.delete(key)
        except Exception:
            pass

    def reap_exits(self) -> int:
        """Drop dead managed processes from Redis; return count reaped.

        Dead procs are not kept. NODEEXIT is published, then the RUNNINGNODES
        hash is deleted so the panel never lists a corpse.
        """
        reaped = 0
        for entry in list(self.registry.items()):
            code = entry.process.poll()
            if code is None:
                continue
            unique_id = entry.unique_id
            exited_at = _utc_now_iso()
            exit_code = str(code)
            entry.append_log_note(
                f"--- exit unique_id={unique_id} exit_code={exit_code} at {exited_at} ---"
            )
            entry.close_log()
            self.registry.pop(unique_id)
            key = running_nodes_key(unique_id)
            try:
                if self.persistent.exists(key):
                    self.ephemeral.xadd(
                        NODEEXIT_STREAM,
                        {
                            "unique_id": unique_id,
                            "node_endpoint": entry.node_endpoint,
                            "exit_code": exit_code,
                            "log_path": entry.log_path,
                            "exited_at": exited_at,
                        },
                    )
                    self.persistent.delete(key)
                reaped += 1
                log.info(
                    "Reaped %s unique_id=%s exit_code=%s log=%s",
                    entry.node_endpoint,
                    unique_id,
                    exit_code,
                    entry.log_path,
                )
            except Exception:
                log.exception(
                    "Failed to record exit for %s unique_id=%s",
                    entry.node_endpoint,
                    unique_id,
                )
        reaped += self.reconcile_stale()
        return reaped

    def reconcile_stale(self) -> int:
        """Delete RUNNINGNODES hashes whose OS pid and heartbeat are both dead.

        After a Supervisor restart the in-memory registry is empty, but DB 1
        still holds hashes from the previous process. Those are the "alive
        nodes that are actually dead" the panel used to show.
        """
        dropped = 0
        try:
            keys = list(self.persistent.scan_iter(match="RUNNINGNODES:*", count=100))
        except Exception:
            return 0
        in_memory = set(self.registry.all_ids())
        for key in keys:
            try:
                data = self.persistent.hgetall(key)
            except Exception:
                continue
            if not data:
                continue
            uid = (data.get("unique_id") or "").strip()
            if uid and uid in in_memory:
                continue
            if is_reported_node_alive(data, self.persistent):
                continue
            try:
                self.persistent.delete(key)
                dropped += 1
                log.info(
                    "Dropped stale RUNNINGNODES unique_id=%s pid=%s",
                    uid or key,
                    data.get("PID"),
                )
            except Exception:
                log.exception("Failed to drop stale RUNNINGNODES %s", key)
        return dropped
