"""Spin Docker sandboxes that mount Floor worktrees and run LiveHarness."""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
from pathlib import Path

from PlantManager.env import load_plant_env

load_plant_env()

log = logging.getLogger("pool")

IMAGE_NAME = os.environ.get("PLANT_IMAGE", "plant-agent:latest")
NETWORK_NAME = os.environ.get("PLANT_NETWORK", "plant-net")
DEFAULT_CONTAINER_REDIS_URL = "redis://host.docker.internal:6379/0"
LOCAL_REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


def _docker(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args],
        check=check,
        text=True,
        capture_output=True,
    )


def require_local_redis() -> None:
    """Ping local Redis; exit if it is not reachable. Never starts Redis."""
    from LiveHarness.harness import connect_redis

    connect_redis(LOCAL_REDIS_URL)
    log.info("Connected to local Redis at %s", LOCAL_REDIS_URL)


def ensure_network() -> None:
    result = _docker(["network", "ls", "--format", "{{.Name}}"], check=True)
    names = set(result.stdout.split())
    if NETWORK_NAME not in names:
        log.info("Creating docker network %s", NETWORK_NAME)
        _docker(["network", "create", NETWORK_NAME])


def build_image(project_root: Path) -> None:
    log.info("Building image %s", IMAGE_NAME)
    proc = subprocess.run(
        ["docker", "build", "-t", IMAGE_NAME, str(project_root)],
        check=False,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"docker build failed with code {proc.returncode}")


def container_name(repo: str, ticket: str) -> str:
    safe = f"pm-{repo}-ticket-{ticket}".lower().replace("_", "-")
    return re.sub(r"[^a-zA-Z0-9_.-]", "-", safe)


def find_bare_dir(worktree: Path) -> Path:
    """Locate Floor/<repo>/.bare by walking up from a worktree path."""
    resolved = worktree.resolve()
    for parent in [resolved, *resolved.parents]:
        candidate = parent / ".bare"
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError(
        f"No .bare directory found above worktree {worktree}. "
        "Floor repos must keep .bare next to wt/."
    )


def start_ticket_sandbox(
    *,
    repo: str,
    ticket: str,
    host_worktree: Path,
    agent_dir: Path,
    guid: str,
    ticket_id: str,
    api_key: str | None = None,
) -> str:
    """Mount a host worktree + its .bare and run one-shot LiveHarness.

    Used for WORKORDER jobs (new ticket trees and existing ``wt`` merges).
    Host paths are passed via env so LiveHarness can publish FINISHED:<REPO>
    with absolute paths MergeManager understands.
    ``.bare`` is mounted at /bare so linked worktrees can resolve gitdir
    pointers inside the Linux container (LiveHarness rewrites them for the run).
    Uses --rm so the container is removed when LiveHarness exits.
    Returns the container name.
    """
    key = api_key or os.environ.get("CURSOR_API_KEY")
    if not key:
        print("CURSOR_API_KEY is required to start sandboxes", file=sys.stderr)
        raise SystemExit(1)

    ensure_network()

    name = container_name(repo, ticket)
    host_path = str(host_worktree.resolve())
    host_agent = str(agent_dir.resolve())
    bare_dir = find_bare_dir(host_worktree)
    host_bare = str(bare_dir.resolve())

    existing = _docker(["inspect", "-f", "{{.State.Running}}", name], check=False)
    if existing.returncode == 0:
        log.info("Removing existing container %s before restart", name)
        _docker(["rm", "-f", name], check=False)

    redis_url = os.environ.get("REDIS_URL_CONTAINER", DEFAULT_CONTAINER_REDIS_URL)
    args = [
        "run",
        "-d",
        "--rm",
        "--name",
        name,
        "--network",
        NETWORK_NAME,
        "--add-host=host.docker.internal:host-gateway",
        "-e",
        f"CURSOR_API_KEY={key}",
        "-e",
        f"REDIS_URL={redis_url}",
        "-e",
        f"GUID={guid}",
        "-e",
        f"REPO_NAME={repo}",
        "-e",
        f"TICKET={ticket}",
        "-e",
        f"TICKET_ID={ticket_id}",
        "-e",
        f"HOST_WT={host_path}",
        "-e",
        f"HOST_AGENT_DIR={host_agent}",
        "-e",
        "WORKSPACE=/workspace",
        "-e",
        "BARE_MOUNT=/bare",
        "-v",
        f"{host_path}:/workspace",
        "-v",
        f"{host_bare}:/bare",
        IMAGE_NAME,
    ]
    log.info(
        "Starting sandbox %s mounting wt=%s bare=%s guid=%s ticket_id=%s",
        name,
        host_path,
        host_bare,
        guid,
        ticket_id,
    )
    _docker(args)
    return name
