"""Heartbeat + Redis kill-switch for MegaDesk BE processes.

Install one ``NodeRuntime`` at BE process start (see ``from_env``). It:

1. Writes ``NODEHB:<unique_id>`` on the process persistent DB every 5s with the real
   ``os.getpid()`` and status, so Supervisor can tell a live node from a stale
   ``RUNNINGNODES`` hash.
2. Polls ``NODE:SHUTDOWN`` (global) and ``NODE:SHUTDOWN:<unique_id>``. A value
   of ``1`` stops the process. Losing Redis also stops the process — that is
   the informal "kill everything" path when Redis is downed.
3. Exposes ``should_stop()`` for cooperative loops, and force-exits after a
   short grace if the main thread does not leave on its own.

Supervisor records ``Popen.pid`` (the launched ``python.exe``). The heartbeat
pid is ``os.getpid()`` inside the node. Those match when ``BeSpec.argv`` is
``[sys.executable, "-u", "-m", ...]`` with no shell. If they differ, Supervisor
trusts the heartbeat pid as the real node process.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

try:
    import redis
except ImportError:  # pragma: no cover
    redis = None  # type: ignore

from megadesk_contracts.supervisor_client import (
    resolve_persistent_db,
    redis_connect,
    resolve_redis_url,
)

log = logging.getLogger("megadesk_contracts.node_runtime")

NODE_HEARTBEAT_PREFIX = "NODEHB:"
NODE_SHUTDOWN_KEY = "NODE:SHUTDOWN"
NODE_SHUTDOWN_PREFIX = "NODE:SHUTDOWN:"
HEARTBEAT_INTERVAL_SEC = 5.0
HEARTBEAT_TTL_SEC = 15
HEARTBEAT_GRACE_SEC = 20.0
SHUTDOWN_FORCE_EXIT_SEC = 3.0
ENV_UNIQUE_ID = "MEGADESK_UNIQUE_ID"
ENV_NODE = "MEGADESK_NODE"

_CURRENT: Optional["NodeRuntime"] = None

# Windows: GetExitCodeProcess STILL_ACTIVE
_STILL_ACTIVE = 259


def heartbeat_key(unique_id: str) -> str:
    return f"{NODE_HEARTBEAT_PREFIX}{unique_id}"


def shutdown_key(unique_id: str) -> str:
    return f"{NODE_SHUTDOWN_PREFIX}{unique_id}"


def pid_is_alive(pid: int) -> bool:
    """True if ``pid`` is a live OS process. Works on Windows and POSIX."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return int(exit_code.value) == _STILL_ACTIVE
            return True
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(int(pid), 0)
    except OSError:
        return False
    return True


def _parse_int(raw: object) -> int:
    try:
        return int(str(raw or "").strip() or "0")
    except (TypeError, ValueError):
        return 0


def _launched_age_sec(launched_at: str) -> Optional[float]:
    text = (launched_at or "").strip()
    if not text:
        return None
    try:
        when = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - when).total_seconds())
    except ValueError:
        return None


def is_reported_node_alive(
    entry: dict[str, str],
    persistent: object,
    *,
    now: Optional[float] = None,
) -> bool:
    """True if a RUNNINGNODES hash still corresponds to a live node.

    Prefers the heartbeat pid (the process that called ``os.getpid()``) over
    the Supervisor ``Popen`` pid. A freshly launched node is trusted on the
    Popen pid alone until the first heartbeat is due.
    """
    _ = now
    uid = (entry.get("unique_id") or "").strip()
    popen_pid = _parse_int(entry.get("PID") or entry.get("pid"))
    hb: dict[str, str] = {}
    if uid and persistent is not None:
        try:
            hb = persistent.hgetall(heartbeat_key(uid)) or {}  # type: ignore[union-attr]
        except Exception:
            hb = {}
    hb_pid = _parse_int(hb.get("pid"))

    if hb_pid and pid_is_alive(hb_pid):
        return True
    if popen_pid and pid_is_alive(popen_pid):
        if hb:
            return True
        age = _launched_age_sec(entry.get("launched_at") or "")
        if age is None or age < HEARTBEAT_GRACE_SEC:
            return True
    return False


def request_shutdown(persistent: object, unique_id: Optional[str] = None) -> None:
    """Set the kill switch. ``unique_id=None`` stops every NodeRuntime."""
    if unique_id:
        persistent.set(shutdown_key(unique_id), "1")  # type: ignore[union-attr]
    else:
        persistent.set(NODE_SHUTDOWN_KEY, "1")  # type: ignore[union-attr]


def clear_shutdown(persistent: object, unique_id: Optional[str] = None) -> None:
    if unique_id:
        persistent.delete(shutdown_key(unique_id))  # type: ignore[union-attr]
    else:
        persistent.delete(NODE_SHUTDOWN_KEY)  # type: ignore[union-attr]


def shutdown_is_set(persistent: object, unique_id: str = "") -> bool:
    try:
        if persistent.get(NODE_SHUTDOWN_KEY) == "1":  # type: ignore[union-attr]
            return True
        if unique_id and persistent.get(shutdown_key(unique_id)) == "1":  # type: ignore[union-attr]
            return True
    except Exception:
        return True
    return False


def node_should_stop() -> bool:
    """Cooperative stop check for BE poll loops. False when no runtime is installed."""
    runtime = _CURRENT
    return runtime is not None and runtime.should_stop()


class NodeRuntime:
    """Process-wide heartbeat + kill-switch. Use as a context manager around ``main``."""

    def __init__(
        self,
        name: str,
        *,
        unique_id: str = "",
        redis_url: Optional[str] = None,
        interval: float = HEARTBEAT_INTERVAL_SEC,
        force_exit: bool = True,
    ) -> None:
        if redis is None:
            raise RuntimeError("redis package is required for NodeRuntime")
        self.name = (name or "").strip() or "unknown"
        self.unique_id = (unique_id or "").strip()
        self.pid = os.getpid()
        self.interval = interval
        self.force_exit = force_exit
        self.redis_url = resolve_redis_url(redis_url)
        self.persistent = redis_connect(
            self.redis_url, db=resolve_persistent_db(self.redis_url)
        )
        self._stop = threading.Event()
        self._should_stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._force_deadline: Optional[float] = None

    @classmethod
    def from_env(
        cls,
        name: Optional[str] = None,
        **kwargs: object,
    ) -> "NodeRuntime":
        node = (name or os.environ.get(ENV_NODE) or "").strip()
        unique_id = (os.environ.get(ENV_UNIQUE_ID) or "").strip()
        if not unique_id:
            unique_id = f"manual-{os.getpid()}"
        return cls(node, unique_id=unique_id, **kwargs)  # type: ignore[arg-type]

    @classmethod
    def current(cls) -> Optional["NodeRuntime"]:
        return _CURRENT

    def start(self) -> None:
        global _CURRENT
        _CURRENT = self
        self._stop.clear()
        self._should_stop.clear()
        self._force_deadline = None
        self.beat()
        self._thread = threading.Thread(
            target=self._loop, name=f"node-runtime-{self.name}", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        global _CURRENT
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
        if self.unique_id:
            try:
                self.persistent.delete(heartbeat_key(self.unique_id))
            except Exception:
                pass
        if _CURRENT is self:
            _CURRENT = None

    def __enter__(self) -> "NodeRuntime":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def should_stop(self) -> bool:
        return self._should_stop.is_set()

    def beat(self) -> None:
        if not self.unique_id:
            return
        self.persistent.hset(
            heartbeat_key(self.unique_id),
            mapping={
                "pid": str(self.pid),
                "status": "running",
                "node": self.name,
                "unique_id": self.unique_id,
                "ts": datetime.now(timezone.utc).isoformat(),
            },
        )
        self.persistent.expire(heartbeat_key(self.unique_id), HEARTBEAT_TTL_SEC)

    def _mark_stop(self, reason: str) -> None:
        if self._should_stop.is_set():
            return
        log.info("NodeRuntime stop: %s", reason)
        self._should_stop.set()
        if self.force_exit and self._force_deadline is None:
            self._force_deadline = time.monotonic() + SHUTDOWN_FORCE_EXIT_SEC

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.persistent.ping()
                if shutdown_is_set(self.persistent, self.unique_id):
                    self._mark_stop("shutdown flag")
                else:
                    self.beat()
            except Exception as exc:
                self._mark_stop(f"redis unreachable: {exc}")
            if (
                self.force_exit
                and self._force_deadline is not None
                and time.monotonic() >= self._force_deadline
            ):
                log.warning("NodeRuntime force-exit after shutdown grace")
                os._exit(0)
            self._stop.wait(self.interval)


def persistent_client(redis_url: Optional[str] = None):
    """Persistent-DB client for kill-switch / heartbeat helpers (scripts and tests)."""
    if redis is None:
        raise RuntimeError("redis package is required")
    url = resolve_redis_url(redis_url)
    return redis_connect(url, db=resolve_persistent_db(url))


def redis_host_reachable(redis_url: Optional[str] = None, timeout: float = 1.0) -> bool:
    if redis is None:
        return False
    url = resolve_redis_url(redis_url)
    parsed = urlparse(url)
    client = redis.Redis(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        db=resolve_persistent_db(url),
        decode_responses=True,
        socket_connect_timeout=timeout,
        socket_timeout=timeout,
    )
    try:
        return bool(client.ping())
    except Exception:
        return False
    finally:
        try:
            client.close()
        except Exception:
            pass
