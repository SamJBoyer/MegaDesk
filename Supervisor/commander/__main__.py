"""Easy start for the GBD Windows commander backend (EE-1).

Usage:
    python -m commander
"""

from __future__ import annotations

import logging
import signal
import sys
import time

from commander.engine import ExecutionEngine
from commander.pubsub_server import CommanderServer
from commander.redis_provision import clear_commander_alive, provision_redis


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    log = logging.getLogger("gbd.commander")

    try:
        realtime = provision_redis()
    except Exception as exc:
        log.error("Redis provision failed: %s", exc)
        return 1

    engine = ExecutionEngine(realtime)
    server = CommanderServer(realtime, engine)
    server.start()
    log.info("GBD commander running. Redis localhost:6379. Ctrl+C to stop.")

    def _shutdown(*_args: object) -> None:
        log.info("Shutting down…")
        server.stop()
        engine.kill_all()
        clear_commander_alive(realtime)
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
