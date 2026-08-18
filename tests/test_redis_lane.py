"""MachineFactory leases Redis pairs; agents do not mark them free."""

import pytest
from megadesk_contracts import (
    AGENT_LANE_EPHEMERAL_DBS,
    LaneBusyError,
    allocate_lane,
    refresh_lane,
    release_lane,
)
from megadesk_contracts.redis_lane import (
    flush_pair,
    lane_by_run_key,
    lane_lease_key,
)

pytestmark = [pytest.mark.redis]


def test_flush_pair_refuses_live_databases() -> None:
    with pytest.raises(ValueError, match="live Redis"):
        flush_pair("redis://localhost:6379/0", 0, 1)


def test_allocate_refresh_release_on_the_injected_client(persistent_client) -> None:
    owner = "run-a"
    ephemeral, persistent = allocate_lane(
        owner=owner,
        lease_client=persistent_client,
        flush=False,
        ttl=30,
    )
    assert ephemeral in AGENT_LANE_EPHEMERAL_DBS
    assert persistent == ephemeral + 1
    assert persistent_client.get(lane_lease_key(ephemeral)) == owner
    assert persistent_client.get(lane_by_run_key(owner)) == str(ephemeral)
    assert refresh_lane(owner=owner, lease_client=persistent_client, ttl=30)
    assert release_lane(owner=owner, lease_client=persistent_client, flush=False)
    assert persistent_client.get(lane_lease_key(ephemeral)) is None
    assert persistent_client.get(lane_by_run_key(owner)) is None


def test_a_second_owner_gets_the_next_lane(persistent_client) -> None:
    first, _ = allocate_lane(
        owner="run-1", lease_client=persistent_client, flush=False
    )
    second, _ = allocate_lane(
        owner="run-2", lease_client=persistent_client, flush=False
    )
    assert first != second
    release_lane(owner="run-1", lease_client=persistent_client, flush=False)
    release_lane(owner="run-2", lease_client=persistent_client, flush=False)


def test_all_lanes_busy_raises(persistent_client) -> None:
    owners = []
    for i, _db in enumerate(AGENT_LANE_EPHEMERAL_DBS):
        owner = f"full-{i}"
        allocate_lane(owner=owner, lease_client=persistent_client, flush=False)
        owners.append(owner)
    with pytest.raises(LaneBusyError):
        allocate_lane(owner="overflow", lease_client=persistent_client, flush=False)
    for owner in owners:
        release_lane(owner=owner, lease_client=persistent_client, flush=False)


def test_sandbox_env_splits_factory_bus_from_subject_lane() -> None:
    from MachineFactoryManager.pool import sandbox_redis_env

    subject, factory = sandbox_redis_env(
        4,
        container_redis_url="redis://host.docker.internal:6379/0",
        factory_ephemeral_db=0,
    )
    assert subject == "redis://host.docker.internal:6379/4"
    assert factory == "redis://host.docker.internal:6379/0"
