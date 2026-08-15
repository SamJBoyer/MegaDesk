"""NodeRuntime heartbeat, kill switch, and Supervisor stale-hash cleanup."""

from __future__ import annotations

import os
import time
import uuid

import pytest

from megadesk_contracts.node_runtime import (
    HEARTBEAT_GRACE_SEC,
    NodeRuntime,
    heartbeat_key,
    is_reported_node_alive,
    pid_is_alive,
    request_shutdown,
    shutdown_key,
)

pytestmark = [pytest.mark.redis]


def test_pid_is_alive_reports_this_process() -> None:
    assert pid_is_alive(os.getpid())
    assert not pid_is_alive(0)
    assert not pid_is_alive(2_000_000_000)


def test_heartbeat_writes_this_pid(persistent_client) -> None:
    uid = f"test-{uuid.uuid4()}"
    runtime = NodeRuntime("probe", unique_id=uid, interval=0.2, force_exit=False)
    runtime.start()
    try:
        key = heartbeat_key(uid)
        deadline = time.time() + 2
        data = {}
        while time.time() < deadline:
            data = persistent_client.hgetall(key)
            if data.get("pid") == str(os.getpid()):
                break
            time.sleep(0.05)
        assert data.get("pid") == str(os.getpid())
        assert data.get("status") == "running"
        assert data.get("node") == "probe"
    finally:
        runtime.stop()
        persistent_client.delete(heartbeat_key(uid))


def test_shutdown_flag_stops_the_runtime(persistent_client) -> None:
    uid = f"test-{uuid.uuid4()}"
    runtime = NodeRuntime("probe", unique_id=uid, interval=0.1, force_exit=False)
    runtime.start()
    try:
        assert not runtime.should_stop()
        request_shutdown(persistent_client, uid)
        deadline = time.time() + 2
        while time.time() < deadline and not runtime.should_stop():
            time.sleep(0.05)
        assert runtime.should_stop()
    finally:
        runtime.stop()
        persistent_client.delete(shutdown_key(uid), heartbeat_key(uid))


def test_stale_runningnodes_hash_is_not_alive(persistent_client) -> None:
    uid = f"test-{uuid.uuid4()}"
    entry = {
        "unique_id": uid,
        "node_endpoint": "probe",
        "PID": "199999999",
        "status": "running",
        "launched_at": "2020-01-01T00:00:00+00:00",
    }
    assert not is_reported_node_alive(entry, persistent_client)


def test_fresh_launch_is_trusted_on_live_popen_pid(persistent_client) -> None:
    from datetime import datetime, timezone

    uid = f"test-{uuid.uuid4()}"
    entry = {
        "unique_id": uid,
        "node_endpoint": "probe",
        "PID": str(os.getpid()),
        "status": "running",
        "launched_at": datetime.now(timezone.utc).isoformat(),
    }
    assert is_reported_node_alive(entry, persistent_client)
    _ = HEARTBEAT_GRACE_SEC
