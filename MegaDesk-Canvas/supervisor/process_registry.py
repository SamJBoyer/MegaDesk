"""Launched-node process registry keyed by unique_id, with graceful→force shutdown."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, TextIO

from megadesk_contracts import BeSpec
from megadesk_contracts.paths import resolve_canvas_root, resolve_logs_root

ENV_UNIQUE_ID = "MEGADESK_UNIQUE_ID"
ENV_NODE = "MEGADESK_NODE"
ENV_LOG_PATH = "MEGADESK_LOG_PATH"


def canvas_root() -> Path:
    """Canvas that owns this Supervisor process (env / cwd, not another worktree)."""
    return resolve_canvas_root()


def logs_root() -> Path:
    return resolve_logs_root()


def instance_log_path(node_endpoint: str, unique_id: str) -> Path:
    """Absolute path for a managed BE instance log file."""
    safe_endpoint = node_endpoint.strip() or "unknown"
    return (logs_root() / safe_endpoint / f"{unique_id}.log").resolve()


def supervisor_self_log_path() -> Path:
    """Absolute path for the Supervisor BE bootstrap log."""
    return (logs_root() / "supervisor" / "supervisor.log").resolve()


@dataclass
class ManagedProcess:
    unique_id: str
    node_endpoint: str
    parameters: str
    command: list[str]
    cwd: str
    process: subprocess.Popen
    log_path: str = ""
    log_handle: Optional[TextIO] = field(default=None, repr=False)
    launched_at: float = field(default_factory=time.time)

    @property
    def alive(self) -> bool:
        return self.process.poll() is None

    @property
    def pid(self) -> int:
        return self.process.pid

    def close_log(self) -> None:
        fh = self.log_handle
        self.log_handle = None
        if fh is None:
            return
        try:
            fh.flush()
        except Exception:
            pass
        try:
            fh.close()
        except Exception:
            pass

    def append_log_note(self, text: str) -> None:
        """Best-effort note into the instance log (e.g. supervisor stop)."""
        if not self.log_path:
            return
        try:
            with open(self.log_path, "a", encoding="utf-8", errors="replace") as fh:
                fh.write(text)
                if not text.endswith("\n"):
                    fh.write("\n")
        except Exception:
            pass


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

    def items(self) -> list[ManagedProcess]:
        return list(self._nodes.values())

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
        entry.append_log_note("[supervisor] stop requested")

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
            entry.close_log()
            return

        if proc.poll() is not None:
            entry.close_log()
            return
        try:
            proc.terminate()
        except Exception:
            pass
        deadline = time.time() + grace_seconds
        while time.time() < deadline:
            if proc.poll() is not None:
                entry.close_log()
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
        entry.close_log()


def launch_spec(
    spec: BeSpec,
    *,
    unique_id: str,
    parameters: str = "",
) -> ManagedProcess:
    """Launch a BeSpec argv as a managed subprocess with file-backed logs."""
    command = list(spec.argv)
    cwd = spec.cwd or None
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]

    log_path = instance_log_path(spec.name, unique_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle: TextIO = open(log_path, "a", encoding="utf-8", errors="replace", buffering=1)

    env = os.environ.copy()
    env[ENV_UNIQUE_ID] = unique_id
    env[ENV_NODE] = spec.name
    env[ENV_LOG_PATH] = str(log_path)

    try:
        proc = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            creationflags=creationflags,
        )
    except Exception:
        try:
            log_handle.close()
        except Exception:
            pass
        raise

    return ManagedProcess(
        unique_id=unique_id,
        node_endpoint=spec.name,
        parameters=parameters,
        command=command,
        cwd=cwd or "",
        process=proc,
        log_path=str(log_path),
        log_handle=log_handle,
    )
