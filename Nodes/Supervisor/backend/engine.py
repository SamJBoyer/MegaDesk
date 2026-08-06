"""Execution engine: discover BE nodes, launch/kill by unique_id, write RUNNINGNODES."""

from __future__ import annotations

import logging
import threading
import uuid
from typing import Optional

import redis

from megadesk import SUPERVISOR_NODE_NAME, BeSpec, discover_backends, get_backend

from backend.process_registry import ProcessRegistry, launch_spec
from backend.redis_provision import running_nodes_key

log = logging.getLogger("gbd.supervisor")


class NodeLaunchError(Exception):
    """Unknown node endpoint or launch failure."""


class NodeKillError(Exception):
    """Unknown unique_id or endpoint mismatch."""


class ExecutionEngine:
    def __init__(self, realtime: redis.Redis, registry: Optional[ProcessRegistry] = None) -> None:
        self.realtime = realtime
        self.registry = registry or ProcessRegistry()
        self._backends: dict[str, BeSpec] = {}
        self._lock = threading.RLock()
        self.discover_backends()

    def discover_backends(self) -> dict[str, BeSpec]:
        """Refresh the in-memory map of name → BeSpec from MegaDesk.nodes.

        Excludes the Supervisor BeSpec itself — the BE must not launch
        another supervisor via ``LAUNCHREQUEST``.
        """
        with self._lock:
            self._backends = {
                name: spec
                for name, spec in discover_backends().items()
                if name != SUPERVISOR_NODE_NAME
            }
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
            if spec is not None and spec.name != SUPERVISOR_NODE_NAME:
                with self._lock:
                    self._backends[spec.name] = spec
        if spec is None or spec.name == SUPERVISOR_NODE_NAME:
            raise NodeLaunchError(f"Unknown BE node: {node_endpoint}")
        return spec

    def launch(self, node_endpoint: str, parameters: str = "") -> str:
        """Launch a BE process; returns the assigned unique_id."""
        node_endpoint = node_endpoint.strip()
        if not node_endpoint:
            raise NodeLaunchError("Empty node_endpoint")
        if node_endpoint == SUPERVISOR_NODE_NAME:
            raise NodeLaunchError("Cannot launch supervisor via LAUNCHREQUEST")

        spec = self._resolve_spec(node_endpoint)
        unique_id = str(uuid.uuid4())
        entry = launch_spec(spec, unique_id=unique_id, parameters=parameters)
        self.registry.store(entry)
        self.realtime.hset(
            running_nodes_key(unique_id),
            mapping={
                "node_endpoint": entry.node_endpoint,
                "unique_id": unique_id,
                "parameters": parameters,
                "PID": str(entry.pid),
            },
        )
        log.info(
            "Launched %s unique_id=%s pid=%s argv=%s",
            entry.node_endpoint,
            unique_id,
            entry.pid,
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
            # Stale hash without in-memory process — still clear Redis.
            key = running_nodes_key(unique_id)
            stored = self.realtime.hgetall(key)
            if not stored:
                raise NodeKillError(f"No managed process for unique_id: {unique_id}")
            if node_endpoint and stored.get("node_endpoint") != node_endpoint:
                raise NodeKillError(
                    f"node_endpoint mismatch for {unique_id}: "
                    f"expected {stored.get('node_endpoint')!r}, got {node_endpoint!r}"
                )
            self.realtime.delete(key)
            log.info("Cleared stale RUNNINGNODES for unique_id=%s", unique_id)
            return

        if node_endpoint and entry.node_endpoint != node_endpoint:
            raise NodeKillError(
                f"node_endpoint mismatch for {unique_id}: "
                f"expected {entry.node_endpoint!r}, got {node_endpoint!r}"
            )

        self.registry.stop(unique_id)
        self.realtime.delete(running_nodes_key(unique_id))
        log.info("Killed %s unique_id=%s", entry.node_endpoint, unique_id)

    def kill_all(self) -> None:
        ids = self.registry.all_ids()
        self.registry.kill_all()
        for unique_id in ids:
            try:
                self.realtime.delete(running_nodes_key(unique_id))
            except Exception:
                pass
