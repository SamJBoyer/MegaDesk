"""Execution engine: discover BE nodes, launch/stop by name, kill all."""

from __future__ import annotations

import logging
import threading
from typing import Optional

import redis

from megadesk import SUPERVISOR_NODE_NAME, BeSpec, discover_backends, get_backend

from commander.process_registry import ProcessRegistry, launch_spec

log = logging.getLogger("gbd.commander")


class NodeLaunchError(Exception):
    """Unknown node name or launch failure."""


class ExecutionEngine:
    def __init__(self, realtime: redis.Redis, registry: Optional[ProcessRegistry] = None) -> None:
        self.realtime = realtime
        self.registry = registry or ProcessRegistry()
        self._backends: dict[str, BeSpec] = {}
        self._lock = threading.RLock()
        self.discover_backends()

    def discover_backends(self) -> dict[str, BeSpec]:
        """Refresh the in-memory map of name → BeSpec from MegaDesk.nodes.

        Excludes the Supervisor BeSpec itself — the commander must not launch
        another commander via ``launch_node``.
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

    def launch(self, name: str) -> None:
        """Launch (or replace) the BE process for ``name``."""
        name = name.strip()
        if not name:
            raise NodeLaunchError("Empty node name")
        if name == SUPERVISOR_NODE_NAME:
            raise NodeLaunchError("Cannot launch supervisor via launch_node")

        with self._lock:
            spec = self._backends.get(name)
        if spec is None:
            # Refresh once in case a node was installed after startup.
            self.discover_backends()
            with self._lock:
                spec = self._backends.get(name)
        if spec is None:
            # Direct entry-point lookup as a last resort.
            spec = get_backend(name)
            if spec is not None:
                with self._lock:
                    self._backends[spec.name] = spec
        if spec is None:
            raise NodeLaunchError(f"Unknown BE node: {name}")

        entry = launch_spec(spec)
        self.registry.store(entry)
        log.info("Launched BE node %s pid=%s argv=%s", spec.name, entry.process.pid, spec.argv)

    def stop(self, name: str) -> None:
        name = name.strip()
        if not name:
            raise NodeLaunchError("Empty node name")
        stopped = self.registry.stop(name)
        if not stopped:
            raise NodeLaunchError(f"No managed process for node: {name}")
        log.info("Stopped BE node %s", name)

    def kill_all(self) -> None:
        self.registry.kill_all()
