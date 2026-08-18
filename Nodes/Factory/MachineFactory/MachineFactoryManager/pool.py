"""Spin Docker sandboxes that mount Floor worktrees and run AgentHandler."""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

from megadesk_contracts import DEFAULT_REDIS_URL, REDIS_DB_EPHEMERAL, redis_url_with_db
from megadesk_contracts.agent_audit import agent_audit_bind_args

log = logging.getLogger("pool")

IMAGE_NAME = os.environ.get("MACHINE_FACTORY_IMAGE", "machine-factory-agent:latest")
NETWORK_NAME = os.environ.get("MACHINE_FACTORY_NETWORK", "machine-factory-net")
DEFAULT_CONTAINER_REDIS_URL = "redis://host.docker.internal:6379/0"
LOCAL_REDIS_URL = os.environ.get("REDIS_URL", DEFAULT_REDIS_URL)

# Labelled with its run key so a restarted manager can still find, follow and
# stop a sandbox it did not start. The alternative — remembering container names
# in memory — loses every live run the moment the BE is bounced.
RUN_KEY_LABEL = "megadesk.run_key"
CONTAINER_NAME_PREFIX = "mf-"


def _docker(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a docker CLI command; log stderr/stdout when check=True and it fails."""
    try:
        return subprocess.run(
            ["docker", *args],
            check=check,
            text=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or "").strip()
        out = (exc.stdout or "").strip()
        if err:
            log.error("docker %s failed (code=%s): %s", args[0], exc.returncode, err)
        if out:
            log.error("docker %s stdout: %s", args[0], out)
        raise


def _follow_container_logs(container_name: str) -> None:
    """Stream docker logs into the MachineFactory logger until the container exits."""

    def _reader() -> None:
        prefix = f"sandbox[{container_name}]:"
        try:
            proc = subprocess.Popen(
                ["docker", "logs", "-f", "--timestamps", container_name],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            log.error("%s failed to start docker logs follow: %s", prefix, exc)
            return

        assert proc.stdout is not None
        try:
            while True:
                line = proc.stdout.readline()
                if not line:
                    break
                text = line.rstrip("\n\r")
                if not text:
                    continue
                # AgentHandler errors use ERROR/CRITICAL; surface those at error.
                upper = text.upper()
                if " ERROR " in f" {upper} " or " CRITICAL " in f" {upper} ":
                    log.error("%s %s", prefix, text)
                else:
                    log.info("%s %s", prefix, text)
        except OSError as exc:
            log.warning("%s log follow interrupted: %s", prefix, exc)
        finally:
            try:
                proc.stdout.close()
            except OSError:
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            log.info("%s log follow ended", prefix)

    thread = threading.Thread(
        target=_reader,
        name=f"docker-logs-{container_name}",
        daemon=True,
    )
    thread.start()


def sandbox_redis_env(
    ephemeral_db: int,
    *,
    container_redis_url: str | None = None,
    factory_ephemeral_db: int = REDIS_DB_EPHEMERAL,
) -> tuple[str, str]:
    """Subject REDIS_URL and factory IPC URL for a sandbox.

    AgentHandler talks to the factory process's ephemeral DB (live 0, or the
    host-pytest pair if that is who launched it). The agent's MegaDesk uses
    the leased even DB.
    """
    base = container_redis_url or os.environ.get(
        "REDIS_URL_CONTAINER", DEFAULT_CONTAINER_REDIS_URL
    )
    factory_url = redis_url_with_db(base, factory_ephemeral_db)
    subject_url = redis_url_with_db(base, ephemeral_db)
    return subject_url, factory_url


def require_local_redis() -> None:
    """Ping local Redis; exit if it is not reachable. Never starts Redis."""
    from AgentHandler.handler import connect_redis

    connect_redis(LOCAL_REDIS_URL)
    log.info("Connected to local Redis at %s", LOCAL_REDIS_URL)


def ensure_network() -> None:
    result = _docker(["network", "ls", "--format", "{{.Name}}"], check=True)
    names = set(result.stdout.split())
    if NETWORK_NAME not in names:
        log.info("Creating docker network %s", NETWORK_NAME)
        _docker(["network", "create", NETWORK_NAME])


def build_image(node_root: Path) -> None:
    """Build the sandbox image, with the worktree root as the build context.

    The context is deliberately wider than this node: the sandbox installs
    ``megadesk-contracts`` and reads the same wire module the manager writes to.
    Shipping a copy of that module inside the image instead would give the two
    ends of one handshake two definitions of it, which is the failure this
    refactor removed.
    """
    # Nodes/Factory/<node> -> worktree root.
    context = node_root.parents[2]
    log.info("Building image %s (context=%s)", IMAGE_NAME, context)
    proc = subprocess.run(
        [
            "docker",
            "build",
            "-t",
            IMAGE_NAME,
            "-f",
            str(node_root / "Dockerfile"),
            str(context),
        ],
        check=False,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"docker build failed with code {proc.returncode}")


def container_name(repo: str, ticket: str) -> str:
    safe = f"{CONTAINER_NAME_PREFIX}{repo}-ticket-{ticket}".lower().replace("_", "-")
    return re.sub(r"[^a-zA-Z0-9_.-]", "-", safe)


def container_for_run(run_key: str) -> str:
    """The sandbox carrying this run key, or "" if none is left running."""
    result = _docker(
        [
            "ps",
            "--filter",
            f"label={RUN_KEY_LABEL}={run_key}",
            "--format",
            "{{.Names}}",
        ],
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip().splitlines()[0].strip() if result.stdout.strip() else ""


def container_is_running(name: str) -> bool:
    result = _docker(["inspect", "-f", "{{.State.Running}}", name], check=False)
    return result.returncode == 0 and result.stdout.strip() == "true"


def remove_container(name: str) -> None:
    _docker(["rm", "-f", name], check=False)


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
    ephemeral_db: int,
    factory_ephemeral_db: int = REDIS_DB_EPHEMERAL,
    api_key: str | None = None,
) -> str:
    """Mount a host worktree + its .bare and run one-shot AgentHandler.

    Used for WORKORDER jobs (new ticket trees and existing ``wt`` merges).
    Host paths are passed via env so AgentHandler can publish FINISHED:<REPO>
    with absolute paths MergeManager understands.
    ``.bare`` is mounted at /bare so linked worktrees can resolve gitdir
    pointers inside the Linux container (AgentHandler rewrites them for the run).
    The run's audit file (``Logs/{session}/agent-{guid}.md``) is bind-mounted as
    a single file so the sandbox can stream progress without seeing other logs.
    Uses --rm so the container is removed when AgentHandler exits.
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

    if _docker(["inspect", "-f", "{{.State.Running}}", name], check=False).returncode == 0:
        log.info("Removing existing container %s before restart", name)
        remove_container(name)

    redis_url = os.environ.get("REDIS_URL_CONTAINER", DEFAULT_CONTAINER_REDIS_URL)
    subject_url, factory_url = sandbox_redis_env(
        ephemeral_db,
        container_redis_url=redis_url,
        factory_ephemeral_db=factory_ephemeral_db,
    )
    args = [
        "run",
        "-d",
        "--rm",
        "--name",
        name,
        "--network",
        NETWORK_NAME,
        "--add-host=host.docker.internal:host-gateway",
        "--label",
        f"{RUN_KEY_LABEL}={guid}",
        "-e",
        f"CURSOR_API_KEY={key}",
        "-e",
        f"REDIS_URL={subject_url}",
        "-e",
        f"MEGADESK_FACTORY_REDIS_URL={factory_url}",
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
        *agent_audit_bind_args(guid),
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
    _follow_container_logs(name)
    return name
