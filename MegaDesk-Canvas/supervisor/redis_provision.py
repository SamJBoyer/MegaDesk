"""Attach to Redis via REDIS_URL or provision Docker Redis + Insights.

Redis databases:
  ephemeral — control plane (LAUNCHREQUEST / KILLREQUEST / NODEEXIT, node streams)
  persistent — supervisor state (singleton, RUNNINGNODES, alive heartbeat)
  Live pair is (0, 1); ``resolve_redis_pair`` selects the pair for this process.

Connection standard: ``REDIS_URL`` (default ``redis://localhost:6379/0``).
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from typing import Optional

import redis
from megadesk_contracts.supervisor_client import (
    DEFAULT_REDIS_URL,
    KILLREQUEST_STREAM,
    LAUNCHREQUEST_STREAM,
    SUPERVISOR_ALIVE_KEY,
    SUPERVISOR_SINGLETON_KEY,
    resolve_ephemeral_db,
    resolve_persistent_db,
    resolve_redis_url,
    redis_url_host_port,
    redis_connect,
)

REDIS_CONTAINER = "megadesk-redis"
INSIGHTS_CONTAINER = "megadesk-redis-insight"
INSIGHTS_PORT = 5540

ALIVE_TTL_SECONDS = 5
SINGLETON_TTL_SECONDS = 10

CONSUMER_GROUP = "supervisor"
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def ping_redis(redis_url: Optional[str] = None, timeout: float = 1.0) -> bool:
    url = resolve_redis_url(redis_url)
    try:
        client = redis_connect(
            url,
            db=resolve_ephemeral_db(url),
            decode_responses=False,
            socket_connect_timeout=timeout,
        )
        return bool(client.ping())
    except Exception:
        return False


def _docker_available() -> bool:
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        return result.returncode == 0
    except Exception:
        return False


def _container_running(name: str) -> bool:
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", name],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip().lower() == "true"


def _ensure_container(name: str, run_args: list[str]) -> None:
    inspect = subprocess.run(
        ["docker", "inspect", name],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if inspect.returncode == 0:
        if not _container_running(name):
            subprocess.run(["docker", "start", name], check=True, capture_output=True, text=True)
        return
    subprocess.run(["docker", "run", "-d", "--name", name, *run_args], check=True, capture_output=True, text=True)


@dataclass
class RedisHandles:
    """Ephemeral + persistent clients on the same Redis server (process pair)."""

    ephemeral: redis.Redis
    persistent: redis.Redis
    redis_url: str = DEFAULT_REDIS_URL


def connect_handles(redis_url: Optional[str] = None) -> RedisHandles:
    url = resolve_redis_url(redis_url)
    return RedisHandles(
        ephemeral=redis_connect(url, db=resolve_ephemeral_db(url)),
        persistent=redis_connect(url, db=resolve_persistent_db(url)),
        redis_url=url,
    )


def provision_redis(redis_url: Optional[str] = None) -> RedisHandles:
    """Prefer existing Redis at REDIS_URL; otherwise start Docker Redis + Insights.

    Docker auto-provision only runs when the URL host is loopback.
    """
    url = resolve_redis_url(redis_url)
    if ping_redis(url):
        return connect_handles(url)

    host, port = redis_url_host_port(url)
    if host not in _LOOPBACK_HOSTS:
        raise RuntimeError(
            f"Redis is not reachable at {url}. "
            "Start Redis or set REDIS_URL to a reachable server."
        )

    if not _docker_available():
        raise RuntimeError(
            f"Redis is not reachable at {url} and Docker is unavailable. "
            "Start Redis locally, install/start Docker, or set REDIS_URL."
        )

    _ensure_container(
        REDIS_CONTAINER,
        [
            "-p",
            f"{port}:6379",
            "redis:7",
        ],
    )
    _ensure_container(
        INSIGHTS_CONTAINER,
        [
            "-p",
            f"{INSIGHTS_PORT}:5540",
            "redis/redisinsight:latest",
        ],
    )

    deadline = time.time() + 30
    while time.time() < deadline:
        if ping_redis(url):
            return connect_handles(url)
        time.sleep(0.5)

    raise RuntimeError(f"Docker Redis started but {url} never became reachable")


def mark_supervisor_alive(persistent: redis.Redis, ttl: int = ALIVE_TTL_SECONDS) -> None:
    persistent.set(SUPERVISOR_ALIVE_KEY, "1", ex=ttl)


def clear_supervisor_alive(persistent: Optional[redis.Redis]) -> None:
    if persistent is None:
        return
    try:
        persistent.delete(SUPERVISOR_ALIVE_KEY)
    except Exception:
        pass


def is_supervisor_alive(persistent: Optional[redis.Redis] = None) -> bool:
    try:
        url = resolve_redis_url()
        c = persistent or redis_connect(url, db=resolve_persistent_db(url))
        return c.exists(SUPERVISOR_ALIVE_KEY) == 1
    except Exception:
        return False


def acquire_supervisor_singleton(
    persistent: redis.Redis,
    *,
    owner: Optional[str] = None,
    ttl: int = SINGLETON_TTL_SECONDS,
) -> bool:
    """Claim the supervisor singleton on db1. Only one BE may hold it.

    If the key exists but the alive heartbeat is gone, the lock is treated as
    stale and stolen (crash recovery).
    """
    token = owner or str(os.getpid())
    if persistent.set(SUPERVISOR_SINGLETON_KEY, token, nx=True, ex=ttl):
        return True
    if is_supervisor_alive(persistent):
        return False
    # Stale lock — previous supervisor died without clearing.
    persistent.set(SUPERVISOR_SINGLETON_KEY, token, ex=ttl)
    return True


def refresh_supervisor_singleton(
    persistent: redis.Redis,
    *,
    owner: Optional[str] = None,
    ttl: int = SINGLETON_TTL_SECONDS,
) -> bool:
    """Refresh singleton TTL if we still own it. Returns False if ownership lost."""
    token = owner or str(os.getpid())
    current = persistent.get(SUPERVISOR_SINGLETON_KEY)
    if current is None:
        return bool(persistent.set(SUPERVISOR_SINGLETON_KEY, token, nx=True, ex=ttl))
    if current != token:
        return False
    persistent.expire(SUPERVISOR_SINGLETON_KEY, ttl)
    return True


def release_supervisor_singleton(
    persistent: Optional[redis.Redis],
    *,
    owner: Optional[str] = None,
) -> None:
    if persistent is None:
        return
    token = owner or str(os.getpid())
    try:
        current = persistent.get(SUPERVISOR_SINGLETON_KEY)
        if current == token:
            persistent.delete(SUPERVISOR_SINGLETON_KEY)
    except Exception:
        pass


def ensure_consumer_groups(ephemeral: redis.Redis) -> None:
    """Create LAUNCHREQUEST / KILLREQUEST consumer groups on db0 if missing."""
    for stream in (LAUNCHREQUEST_STREAM, KILLREQUEST_STREAM):
        try:
            ephemeral.xgroup_create(stream, CONSUMER_GROUP, id="0", mkstream=True)
        except redis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise
