"""End-to-end smoke test: launch_node plant → stop / KILLALL.

Usage (with commander already running, or this script starts one):
    python -m commander.smoke_test

Requires the Plant package installed into the same env (MegaDesk.nodes entry point).
"""

from __future__ import annotations

import subprocess
import sys
import time

from megadesk import discover_backends

from commander.client import PubSubClient
from commander.paths import REPO_ROOT
from commander.redis_provision import (
    COMMANDER_ALIVE_KEY,
    is_commander_alive,
    provision_redis,
)

NODE_NAME = "plant"


def _plant_pids() -> list[str]:
    ps = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-CimInstance Win32_Process | "
            "Where-Object { "
            "$_.CommandLine -match 'PlantManager' "
            "-and $_.CommandLine -notmatch 'smoke_test' "
            "} | Select-Object -ExpandProperty ProcessId",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return [p.strip() for p in ps.stdout.splitlines() if p.strip().isdigit()]


def _force_kill_plant() -> None:
    for pid in _plant_pids():
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", pid],
            capture_output=True,
            text=True,
            check=False,
        )


def _fail(msg: str) -> int:
    print(f"FAIL: {msg}")
    return 1


def _ok(msg: str) -> None:
    print(f"OK:   {msg}")


def main() -> int:
    print(f"=== Supervisor smoke test (launch_node {NODE_NAME}) ===")
    backends = discover_backends()
    if NODE_NAME not in backends:
        return _fail(
            f"BE node {NODE_NAME!r} not discovered. "
            "Install Plant editable: pip install -e ../Plant (and megadesk)."
        )
    _ok(f"Discovered BE nodes: {', '.join(sorted(backends))}")

    try:
        realtime = provision_redis()
    except Exception as exc:
        return _fail(f"Redis: {exc}")
    _ok("Redis reachable on localhost:6379")

    commander_proc = None
    if not is_commander_alive(realtime):
        print("… starting commander subprocess")
        commander_proc = subprocess.Popen(
            [sys.executable, "-m", "commander"],
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        deadline = time.time() + 15
        while time.time() < deadline and not is_commander_alive(realtime):
            time.sleep(0.2)
        if not is_commander_alive(realtime):
            return _fail("Commander did not become alive")
    _ok("Commander alive")

    client = PubSubClient(caller_identity="smoke-test", timeout=8.0)

    # Clear orphans from prior runs so PID checks are meaningful.
    _force_kill_plant()
    time.sleep(0.5)

    lack = client.launch_node(NODE_NAME)
    if not lack or not lack.startswith("SUCCESS"):
        return _fail(f"launch_node ack={lack}")
    _ok(f"launch_node -> {lack}")

    time.sleep(1.5)
    live = _plant_pids()
    if not live:
        return _fail("PlantManager process not found after launch_node")
    _ok(f"PlantManager process alive pid={live}")

    sack = client.stop_node(NODE_NAME)
    if not sack or not sack.startswith("SUCCESS"):
        return _fail(f"stop_node ack={sack}")
    _ok(f"stop_node -> {sack}")

    deadline = time.time() + 8
    while time.time() < deadline and _plant_pids():
        time.sleep(0.25)
    leftover = _plant_pids()
    if leftover:
        client.killall()
        time.sleep(0.5)
        _force_kill_plant()
        deadline = time.time() + 5
        while time.time() < deadline and _plant_pids():
            time.sleep(0.25)
        leftover = _plant_pids()
        if leftover:
            return _fail(f"PlantManager still alive after stop/KILLALL: {leftover}")
    _ok("PlantManager stopped")

    # Unknown node should FAIL
    bad = client.launch_node("does-not-exist-node")
    if bad and bad.startswith("SUCCESS"):
        return _fail("unknown node unexpectedly launched SUCCESS")
    _ok(f"unknown node -> {bad}")

    if commander_proc is not None:
        commander_proc.terminate()
        try:
            commander_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            commander_proc.kill()
        try:
            realtime.delete(COMMANDER_ALIVE_KEY)
        except Exception:
            pass

    print("=== SMOKE TEST PASSED ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
