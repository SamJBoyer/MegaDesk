"""End-to-end smoke test: LAUNCHREQUEST plant → RUNNINGNODES → KILLREQUEST.

Usage (with Supervisor BE already running, or this script starts one):
    python -m backend.smoke_test

Requires the Plant package installed into the same env (MegaDesk.nodes entry point).
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from megadesk import discover_backends

from backend.client import SupervisorStreamClient
from backend.redis_provision import (
    SUPERVISOR_ALIVE_KEY,
    is_supervisor_alive,
    provision_redis,
    running_nodes_key,
)

NODE_NAME = "plant"
REPO_ROOT = Path(__file__).resolve().parent.parent


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


def _wait_for_running(client: SupervisorStreamClient, endpoint: str, timeout: float = 8.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        matches = [
            e for e in client.list_running() if e.get("node_endpoint") == endpoint
        ]
        if matches:
            return matches[0]
        time.sleep(0.25)
    return None


def main() -> int:
    print(f"=== Supervisor smoke test (LAUNCHREQUEST {NODE_NAME}) ===")
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

    supervisor_proc = None
    if not is_supervisor_alive(realtime):
        print("… starting supervisor BE subprocess")
        supervisor_proc = subprocess.Popen(
            [sys.executable, "-m", "backend"],
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        deadline = time.time() + 15
        while time.time() < deadline and not is_supervisor_alive(realtime):
            time.sleep(0.2)
        if not is_supervisor_alive(realtime):
            return _fail("Supervisor BE did not become alive")
    _ok("Supervisor BE alive")

    client = SupervisorStreamClient()

    # Clear orphans from prior runs so PID checks are meaningful.
    for entry in client.list_running():
        if entry.get("node_endpoint") == NODE_NAME and entry.get("unique_id"):
            client.kill_node(NODE_NAME, entry["unique_id"])
    _force_kill_plant()
    time.sleep(0.5)

    entry_id = client.launch_node(NODE_NAME, parameters="")
    _ok(f"LAUNCHREQUEST queued entry_id={entry_id}")

    running = _wait_for_running(client, NODE_NAME)
    if not running:
        return _fail("RUNNINGNODES entry not found after LAUNCHREQUEST")
    unique_id = running.get("unique_id") or ""
    pid = running.get("PID") or ""
    if not unique_id or not pid:
        return _fail(f"RUNNINGNODES incomplete: {running}")
    _ok(f"RUNNINGNODES unique_id={unique_id} PID={pid}")

    if running.get("status") != "running":
        return _fail(f"expected status=running, got {running.get('status')!r}")
    log_path = running.get("log_path") or ""
    if not log_path:
        return _fail(f"RUNNINGNODES missing log_path: {running}")
    log_file = Path(log_path)
    if not log_file.is_file():
        return _fail(f"log file not created: {log_path}")
    _ok(f"log_path={log_path} ({log_file.stat().st_size} bytes)")

    time.sleep(0.5)
    live = _plant_pids()
    if not live:
        return _fail("PlantManager process not found after launch")
    _ok(f"PlantManager process alive pid={live}")

    client.kill_node(NODE_NAME, unique_id)
    _ok(f"KILLREQUEST queued unique_id={unique_id}")

    deadline = time.time() + 8
    while time.time() < deadline:
        if not client.get_running(unique_id) and not _plant_pids():
            break
        time.sleep(0.25)

    if client.get_running(unique_id):
        realtime.delete(running_nodes_key(unique_id))
        _force_kill_plant()
        return _fail(f"RUNNINGNODES still present after kill: {unique_id}")
    leftover = _plant_pids()
    if leftover:
        _force_kill_plant()
        return _fail(f"PlantManager still alive after kill: {leftover}")
    _ok("PlantManager stopped; RUNNINGNODES cleared")

    # Unknown node should not create a RUNNINGNODES entry
    before = {e.get("unique_id") for e in client.list_running()}
    client.launch_node("does-not-exist-node", parameters="")
    time.sleep(1.0)
    after = [
        e
        for e in client.list_running()
        if e.get("unique_id") not in before
        and e.get("node_endpoint") == "does-not-exist-node"
    ]
    if after:
        return _fail(f"unknown node unexpectedly registered: {after}")
    _ok("unknown node did not register RUNNINGNODES")

    if supervisor_proc is not None:
        supervisor_proc.terminate()
        try:
            supervisor_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            supervisor_proc.kill()
        try:
            realtime.delete(SUPERVISOR_ALIVE_KEY)
        except Exception:
            pass

    print("=== SMOKE TEST PASSED ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
