"""End-to-end smoke test: register/execute ol.yaml → TrialRunnerOL → KILLALL.

Usage (with commander already running, or this script starts one):
    python -m commander.smoke_test
"""

from __future__ import annotations

import subprocess
import sys
import time

from commander.client import PubSubClient
from commander.paths import REPO_ROOT
from commander.redis_provision import (
    COMMANDER_ALIVE_KEY,
    connect_param_db,
    is_commander_alive,
    provision_redis,
)


def _trial_runner_pids() -> list[str]:
    ps = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-CimInstance Win32_Process | "
            "Where-Object { "
            "$_.CommandLine -match 'python(\\.exe)?\\s+-u\\s+trial_runner\\.py' "
            "-and $_.CommandLine -match '-n\\s+TrialRunnerOL' "
            "} | Select-Object -ExpandProperty ProcessId",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return [p.strip() for p in ps.stdout.splitlines() if p.strip().isdigit()]

EXPECTED_PARAMS = {
    "experiment_path": "AS_OL.txt",
    "target_dir": "assets/elbow/track",
    "start": "assets/elbow/halfway.json",
    "threshold": "1",
    "speed": "5",
    "frame_rate": "60",
}


def _fail(msg: str) -> int:
    print(f"FAIL: {msg}")
    return 1


def _ok(msg: str) -> None:
    print(f"OK:   {msg}")


def main() -> int:
    print("=== GBD smoke test (ol.yaml -> TrialRunnerOL) ===")
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
    manifest = str(REPO_ROOT / "ol.yaml")

    # Validate (no GUID)
    vack = client.validate(manifest)
    if not vack or not vack.startswith("SUCCESS"):
        return _fail(f"validate ack={vack}")
    _ok(f"validate → {vack}")

    # Register
    rack = client.register(manifest)
    if not rack or not rack.startswith("SUCCESS "):
        return _fail(f"register ack={rack}")
    guid = rack.split(" ", 1)[1].strip()
    _ok(f"register → GUID {guid}")

    # Execute
    eack = client.execute(guid)
    if not eack or not eack.startswith("SUCCESS"):
        return _fail(f"execute ack={eack}")
    _ok(f"execute → {eack}")

    # Redis params on DB 1
    params_db = connect_param_db()
    key = "PARAMETERS_TrialRunnerOL"
    got = params_db.hgetall(key)
    if not got:
        return _fail(f"missing Redis hash {key} on DB 1")
    for field, expected in EXPECTED_PARAMS.items():
        actual = str(got.get(field, ""))
        if actual != expected:
            return _fail(f"param {field}: expected {expected!r}, got {actual!r}")
    _ok(f"Redis DB1 {key} matches §6.3")

    # Confirm TrialRunnerOL process is alive
    time.sleep(1.5)
    live = _trial_runner_pids()
    if not live:
        return _fail("TrialRunnerOL process not found after execute")
    _ok(f"TrialRunnerOL process alive pid={live}")

    # KILLALL (graceful then force)
    client.killall()
    deadline = time.time() + 8
    while time.time() < deadline and _trial_runner_pids():
        time.sleep(0.25)
    leftover = _trial_runner_pids()
    if leftover:
        return _fail(f"TrialRunnerOL still alive after KILLALL: {leftover}")
    _ok("KILLALL stopped TrialRunnerOL")

    # Invalid validate check
    bad = client.validate(str(REPO_ROOT / "does-not-exist.yaml"))
    if bad and bad.startswith("SUCCESS"):
        return _fail("invalid manifest unexpectedly validated SUCCESS")
    _ok(f"invalid manifest → {bad}")

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
