"""Easy start for the Canvas-owned Supervisor backend.

Usage:
    python -m supervisor
"""

from __future__ import annotations

import os
import signal
import sys
import time

from megadesk_contracts import configure_node_logging

from supervisor.engine import ExecutionEngine
from supervisor.redis_provision import (
    acquire_supervisor_singleton,
    clear_supervisor_alive,
    provision_redis,
    release_supervisor_singleton,
)
from supervisor.stream_server import SupervisorServer


def main() -> int:
    log = configure_node_logging("gbd.supervisor")
    owner = str(os.getpid())

    try:
        handles = provision_redis()
    except Exception as exc:
        log.error("Redis provision failed: %s", exc)
        return 1

    if not acquire_supervisor_singleton(handles.persistent, owner=owner):
        log.error(
            "Supervisor singleton already held — another Supervisor BE is running. Exiting."
        )
        return 2

    engine = ExecutionEngine(handles.ephemeral, handles.persistent)
    server = SupervisorServer(
        handles.ephemeral,
        handles.persistent,
        engine,
        singleton_owner=owner,
    )
    server.start()
    log.info(
        "Supervisor BE running. Redis localhost:6379 db0=streams db1=persistent. Ctrl+C to stop."
    )

    def _shutdown(*_args: object) -> None:
        log.info("Shutting down…")
        server.stop()
        engine.kill_all()
        clear_supervisor_alive(handles.persistent)
        release_supervisor_singleton(handles.persistent, owner=owner)
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        while True:
            time.sleep(1)
            if server._stop.is_set():  # noqa: SLF001 — cooperative exit after lock loss
                _shutdown()
    except KeyboardInterrupt:
        _shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
