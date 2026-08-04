"""Launched-node process registry and graceful→force shutdown (EE-8–10)."""

from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ManagedProcess:
    nickname: str
    command: list[str]
    cwd: str
    process: subprocess.Popen
    launched_at: float = field(default_factory=time.time)

    @property
    def alive(self) -> bool:
        return self.process.poll() is None


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
        existing = self._nodes.get(entry.nickname)
        if existing and existing.alive:
            self._shutdown_one(existing, grace_seconds=2.0)
        self._nodes[entry.nickname] = entry

    def get(self, nickname: str) -> Optional[ManagedProcess]:
        return self._nodes.get(nickname)

    def alive_nodes(self) -> list[ManagedProcess]:
        return [n for n in self._nodes.values() if n.alive]

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
            # Graceful tree signal (WM_CLOSE), brief wait, then force the tree.
            # Always end with /F /T so orphaned children cannot survive.
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


def launch_node(nickname: str, directory: str, target: str) -> ManagedProcess:
    """Launch CLAM PyNode contract: python <target> -n <nickname> -i localhost -p 6379."""
    python = sys.executable or "python"
    command = [
        python,
        "-u",
        target,
        "-n",
        nickname,
        "-i",
        "localhost",
        "-p",
        "6379",
    ]
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]

    proc = subprocess.Popen(
        command,
        cwd=directory,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        creationflags=creationflags,
    )
    return ManagedProcess(
        nickname=nickname,
        command=command,
        cwd=directory,
        process=proc,
    )
