"""Attach to localhost Redis or provision Docker Redis + Insights."""

from __future__ import annotations

import subprocess
import time
from typing import Optional

import redis

REDIS_HOST = "localhost"
REDIS_PORT = 6379
REDIS_CONTAINER = "gbd-redis"
INSIGHTS_CONTAINER = "gbd-redis-insight"
INSIGHTS_PORT = 5540

SUPERVISOR_ALIVE_KEY = "GBD:SUPERVISOR:ALIVE"
ALIVE_TTL_SECONDS = 5

LAUNCHREQUEST_STREAM = "LAUNCHREQUEST"
KILLREQUEST_STREAM = "KILLREQUEST"
CONSUMER_GROUP = "supervisor"
RUNNINGNODES_PREFIX = "RUNNINGNODES:"


def running_nodes_key(unique_id: str) -> str:
    return f"{RUNNINGNODES_PREFIX}{unique_id}"


def ping_redis(host: str = REDIS_HOST, port: int = REDIS_PORT, timeout: float = 1.0) -> bool:
    try:
        client = redis.Redis(host=host, port=port, db=0, socket_connect_timeout=timeout)
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


def provision_redis() -> redis.Redis:
    """Prefer existing localhost Redis; otherwise start Docker Redis + Insights."""
    if ping_redis():
        return redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0, decode_responses=True)

    if not _docker_available():
        raise RuntimeError(
            "Redis is not reachable on localhost:6379 and Docker is unavailable. "
            "Start Redis locally or install/start Docker."
        )

    _ensure_container(
        REDIS_CONTAINER,
        [
            "-p",
            f"{REDIS_PORT}:6379",
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
        if ping_redis():
            return redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0, decode_responses=True)
        time.sleep(0.5)

    raise RuntimeError("Docker Redis started but localhost:6379 never became reachable")


def mark_supervisor_alive(client: redis.Redis, ttl: int = ALIVE_TTL_SECONDS) -> None:
    client.set(SUPERVISOR_ALIVE_KEY, "1", ex=ttl)


def clear_supervisor_alive(client: Optional[redis.Redis]) -> None:
    if client is None:
        return
    try:
        client.delete(SUPERVISOR_ALIVE_KEY)
    except Exception:
        pass


def is_supervisor_alive(client: Optional[redis.Redis] = None) -> bool:
    try:
        c = client or redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0, decode_responses=True)
        return c.exists(SUPERVISOR_ALIVE_KEY) == 1
    except Exception:
        return False


def ensure_consumer_groups(client: redis.Redis) -> None:
    """Create LAUNCHREQUEST / KILLREQUEST consumer groups if missing."""
    for stream in (LAUNCHREQUEST_STREAM, KILLREQUEST_STREAM):
        try:
            client.xgroup_create(stream, CONSUMER_GROUP, id="0", mkstream=True)
        except redis.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise
