#!/usr/bin/env python3
"""Stop every MegaDesk node BE and the Supervisor via the Redis kill switch.

Run this before changing the supervisor or any node that has a BE, so a leftover
process cannot shadow the new code.

    conda activate MEGADESK
    python scripts/down_nodes.py
"""

from __future__ import annotations

import os
import sys
import time

from megadesk_contracts import (
    SUPERVISOR_ALIVE_KEY,
    SUPERVISOR_SINGLETON_KEY,
    SupervisorClient,
    clear_shutdown,
    pid_is_alive,
    request_shutdown,
)
from megadesk_contracts.node_runtime import NODE_HEARTBEAT_PREFIX


def _terminate(pid: int) -> None:
    if pid <= 0 or not pid_is_alive(pid):
        return
    if os.name == "nt":
        os.system(f"taskkill /F /T /PID {pid} >NUL 2>&1")
        return
    try:
        os.kill(pid, 15)
    except OSError:
        return
    deadline = time.time() + 2
    while time.time() < deadline and pid_is_alive(pid):
        time.sleep(0.05)
    if pid_is_alive(pid):
        try:
            os.kill(pid, 9)
        except OSError:
            pass


def main() -> int:
    client = SupervisorClient()
    if not client.redis_ok():
        print("Redis is not reachable — nodes that cannot ping Redis will exit on their own.")
        return 0

    request_shutdown(client.persistent)
    n = client.kill_all_running()
    print(f"shutdown flag set; KILLREQUEST queued for {n} running node(s)")

    deadline = time.time() + 8
    while time.time() < deadline:
        leftover = list(client.persistent.scan_iter(match=f"{NODE_HEARTBEAT_PREFIX}*", count=50))
        if not leftover and not client.list_running():
            break
        time.sleep(0.25)

    owner = client.persistent.get(SUPERVISOR_SINGLETON_KEY) or ""
    try:
        supervisor_pid = int(str(owner).strip() or "0")
    except ValueError:
        supervisor_pid = 0
    if supervisor_pid:
        print(f"stopping supervisor pid={supervisor_pid}")
        _terminate(supervisor_pid)
    client.persistent.delete(SUPERVISOR_ALIVE_KEY)
    client.persistent.delete(SUPERVISOR_SINGLETON_KEY)
    clear_shutdown(client.persistent)
    print("nodes and supervisor are down; shutdown flag cleared")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
