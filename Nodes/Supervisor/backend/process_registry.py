"""Launched-node process registry keyed by unique_id, with graceful→force shutdown."""

from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

from megadesk import BeSpec


@dataclass
class ManagedProcess:
    unique_id: str
    node_endpoint: str
    parameters: str
    command: list[str]
    cwd: str
    process: subprocess.Popen
    launched_at: float = field(default_factory=time.time)

    @property
    def alive(self) -> bool:
        return self.process.poll() is None

    @property
    def pid(self) -> int:
        return self.process.pid


def _taskkill(pid: int, force: bool) -> None:
    args = ["taskkill", "/T", "/PID", str(pid)]
    if force:
        args.insert(1, "/F")
    try:
        subprocess.run(args, capture_output=True, text=True, check=False, timeout=15)
    except Exception:
        pass


class ProcessRegistry:
    def __init__(self) -> None:
        self._nodes: dict[str, ManagedProcess] = {}

    def store(self, entry: ManagedProcess) -> None:
        self._nodes[entry.unique_id] = entry

    def get(self, unique_id: str) -> Optional[ManagedProcess]:
        return self._nodes.get(unique_id)

    def pop(self, unique_id: str) -> Optional[ManagedProcess]:
        return self._nodes.pop(unique_id, None)

    def alive_nodes(self) -> list[ManagedProcess]:
        return [n for n in self._nodes.values() if n.alive]

    def all_ids(self) -> list[str]:
        return list(self._nodes.keys())

    def stop(self, unique_id: str, grace_seconds: float | None = None) -> bool:
        """Stop one managed node by unique_id. Returns True if an entry existed."""
        entry = self._nodes.pop(unique_id, None)
        if entry is None:
            return False
        if grace_seconds is None:
            grace_seconds = 1.0 if sys.platform == "win32" else 3.0
        self._shutdown_one(entry, grace_seconds=grace_seconds)
        return True

    def kill_all(self, grace_seconds: float | None = None) -> None:
        if grace_seconds is None:
            grace_seconds = 1.0 if sys.platform == "win32" else 3.0
        for entry in list(self._nodes.values()):
            self._shutdown_one(entry, grace_seconds=grace_seconds)
        self._nodes.clear()

    @staticmethod
    def _shutdown_one(entry: ManagedProcess, grace_seconds: float) -> None:
        proc = entry.process
        pid = proc.pid

        if sys.platform == "win32":
            if proc.poll() is None:
                _taskkill(pid, force=False)
                deadline = time.time() + grace_seconds
                while time.time() < deadline:
                    if proc.poll() is not None:
                        break
                    time.sleep(0.05)
            _taskkill(pid, force=True)
            try:
                proc.wait(timeout=2)
            except Exception:
                pass
            return

        if proc.poll() is not None:
            return
        try:
            proc.terminate()
        except Exception:
            pass
        deadline = time.time() + grace_seconds
        while time.time() < deadline:
            if proc.poll() is not None:
                return
            time.sleep(0.1)
        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.wait(timeout=2)
        except Exception:
            pass


def launch_spec(
    spec: BeSpec,
    *,
    unique_id: str,
    parameters: str = "",
) -> ManagedProcess:
    """Launch a BeSpec argv as a managed subprocess."""
    command = list(spec.argv)
    cwd = spec.cwd or None
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]

    proc = subprocess.Popen(
        command,
        cwd=cwd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        creationflags=creationflags,
    )
    return ManagedProcess(
        unique_id=unique_id,
        node_endpoint=spec.name,
        parameters=parameters,
        command=command,
        cwd=cwd or "",
        process=proc,
    )
