"""Factory-owned Redis lanes so sandboxed agents do not share live DBs 0/1.

MegaDesk code calls ``resolve_redis_pair`` and never mentions lanes. MachineFactory
is the allocator: it leases an even/odd pair, injects it as the sandbox
``REDIS_URL``, heartbeats the lease, and flushes both DBs on release.

Leases live on this Redis server's live persistent DB (1), not on the process
pair — the allocator has to outlive any one lane. Host pytest owns 14/15 and is
never handed to an agent.
"""

from __future__ import annotations

from typing import Optional

from megadesk_contracts.supervisor_client import (
    REDIS_DB_PERSISTENT,
    redis_connect,
    redis_url_with_db,
    resolve_redis_url,
)

try:
    import redis
except ImportError:  # pragma: no cover
    redis = None  # type: ignore


LANE_LEASE_PREFIX = "MEGADESK:LANE:"
LANE_BY_RUN_PREFIX = "MEGADESK:LANEBYRUN:"
AGENT_LANE_EPHEMERAL_DBS = (2, 4, 6, 8, 10, 12)
LANE_LEASE_TTL_SEC = 60
PROTECTED_REDIS_DBS = frozenset({0, 1})


class LaneBusyError(RuntimeError):
    """Every agent lane is leased."""


def lane_lease_key(ephemeral_db: int) -> str:
    return f"{LANE_LEASE_PREFIX}{int(ephemeral_db)}"


def lane_by_run_key(owner: str) -> str:
    return f"{LANE_BY_RUN_PREFIX}{owner}"


def live_persistent_client(redis_url: Optional[str] = None):
    """The allocator: this server's db 1, regardless of the process pair."""
    if redis is None:
        raise RuntimeError("redis package is required")
    return redis_connect(redis_url, db=REDIS_DB_PERSISTENT)


def flush_pair(
    redis_url: Optional[str],
    ephemeral: int,
    persistent: int,
) -> None:
    """FLUSHDB both DBs of a lane. Refuses to touch live 0/1."""
    if ephemeral in PROTECTED_REDIS_DBS or persistent in PROTECTED_REDIS_DBS:
        raise ValueError(
            f"refusing to flush live Redis DBs {ephemeral}/{persistent}"
        )
    if redis is None:
        raise RuntimeError("redis package is required")
    url = resolve_redis_url(redis_url)
    for db in (ephemeral, persistent):
        client = redis_connect(url, db=db)
        try:
            client.flushdb()
        finally:
            client.close()


def allocate_lane(
    *,
    owner: str,
    redis_url: Optional[str] = None,
    lease_client=None,
    ttl: int = LANE_LEASE_TTL_SEC,
    flush: bool = True,
) -> tuple[int, int]:
    """Claim the next free even/odd pair. Returns ``(ephemeral, persistent)``."""
    token = (owner or "").strip()
    if not token:
        raise ValueError("lane owner is required")
    client = lease_client if lease_client is not None else live_persistent_client(redis_url)
    for ephemeral in AGENT_LANE_EPHEMERAL_DBS:
        key = lane_lease_key(ephemeral)
        if not client.set(key, token, nx=True, ex=ttl):
            continue
        persistent = ephemeral + 1
        client.set(lane_by_run_key(token), str(ephemeral), ex=ttl)
        if flush:
            flush_pair(redis_url, ephemeral, persistent)
        return (ephemeral, persistent)
    raise LaneBusyError("all Redis lanes are in use")


def refresh_lane(
    *,
    owner: str,
    redis_url: Optional[str] = None,
    lease_client=None,
    ttl: int = LANE_LEASE_TTL_SEC,
) -> bool:
    """Extend the TTL if ``owner`` still holds a lane. True when refreshed."""
    token = (owner or "").strip()
    if not token:
        return False
    client = lease_client if lease_client is not None else live_persistent_client(redis_url)
    raw = client.get(lane_by_run_key(token))
    if raw is None:
        return False
    try:
        ephemeral = int(raw)
    except (TypeError, ValueError):
        return False
    key = lane_lease_key(ephemeral)
    if client.get(key) != token:
        return False
    client.expire(key, ttl)
    client.expire(lane_by_run_key(token), ttl)
    return True


def release_lane(
    *,
    owner: str,
    redis_url: Optional[str] = None,
    lease_client=None,
    flush: bool = True,
) -> bool:
    """Drop ``owner``'s lane and flush both DBs. True when a lease was held."""
    token = (owner or "").strip()
    if not token:
        return False
    client = lease_client if lease_client is not None else live_persistent_client(redis_url)
    raw = client.get(lane_by_run_key(token))
    client.delete(lane_by_run_key(token))
    if raw is None:
        return False
    try:
        ephemeral = int(raw)
    except (TypeError, ValueError):
        return False
    key = lane_lease_key(ephemeral)
    if client.get(key) != token:
        return False
    persistent = ephemeral + 1
    client.delete(key)
    if flush:
        flush_pair(redis_url, ephemeral, persistent)
    return True
