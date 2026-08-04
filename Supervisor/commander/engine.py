"""Execution engine: register, validate, execute, kill (EE-5–13)."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from typing import Optional

import redis

from commander.manifest import Manifest, ManifestError, load_and_validate_manifest
from commander.process_registry import ProcessRegistry, launch_node
from commander.redis_provision import connect_param_db


@dataclass
class RegisteredManifest:
    guid: str
    path: str
    manifest: Manifest


class ExecutionEngine:
    def __init__(self, realtime: redis.Redis, registry: Optional[ProcessRegistry] = None) -> None:
        self.realtime = realtime
        self.params = connect_param_db()
        self.registry = registry or ProcessRegistry()
        self._stash: dict[str, RegisteredManifest] = {}
        self._lock = threading.RLock()

    def validate_path(self, path: str) -> Manifest:
        """Dry-run validation (FE-3). Does not stash a GUID."""
        return load_and_validate_manifest(path)

    def register(self, path: str) -> str:
        """Validate and stash under a new GUID (EE-5). Returns GUID."""
        manifest = load_and_validate_manifest(path)
        guid = str(uuid.uuid4())
        with self._lock:
            self._stash[guid] = RegisteredManifest(
                guid=guid,
                path=str(manifest.path),
                manifest=manifest,
            )
        return guid

    def get_registered(self, guid: str) -> Optional[RegisteredManifest]:
        with self._lock:
            return self._stash.get(guid)

    def execute(self, guid: str) -> None:
        """Upload Redis params then launch each node (EE-6, EE-7, EE-12)."""
        with self._lock:
            registered = self._stash.get(guid)
        if registered is None:
            raise ManifestError(f"Unknown or expired GUID: {guid}")

        for node in registered.manifest.nodes:
            self._upload_parameters(node.nickname, node.parameters)
            entry = launch_node(
                nickname=node.nickname,
                directory=str(node.directory),
                target=node.target,
            )
            self.registry.store(entry)

    def kill_all(self) -> None:
        self.registry.kill_all()

    def _upload_parameters(self, nickname: str, parameters: dict[str, str]) -> None:
        key = f"PARAMETERS_{nickname}"
        pipe = self.params.pipeline()
        pipe.delete(key)
        if parameters:
            pipe.hset(key, mapping={k: str(v) for k, v in parameters.items()})
        pipe.execute()
