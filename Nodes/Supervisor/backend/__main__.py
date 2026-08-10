"""Easy start for the MegaDesk Supervisor backend.

Usage:
    python -m backend
"""

from __future__ import annotations

import signal
import sys
import time

from megadesk_contracts import configure_node_logging

from backend.engine import ExecutionEngine
from backend.redis_provision import clear_supervisor_alive, provision_redis
from backend.stream_server import SupervisorServer


def main() -> int:
    log = configure_node_logging("gbd.supervisor")

    try:
        realtime = provision_redis()
    except Exception as exc:
        log.error("Redis provision failed: %s", exc)
        return 1

    engine = ExecutionEngine(realtime)
    server = SupervisorServer(realtime, engine)
    server.start()
    log.info("Supervisor BE running. Redis localhost:6379. Ctrl+C to stop.")

    def _shutdown(*_args: object) -> None:
        log.info("Shutting down…")
        server.stop()
        engine.kill_all()
        clear_supervisor_alive(realtime)
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        _shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
