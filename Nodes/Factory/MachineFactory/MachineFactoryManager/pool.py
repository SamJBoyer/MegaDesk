"""Spin Docker sandboxes that clone a repo and run AgentHandler with a Redis sidecar."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from megadesk_contracts import (
    DEFAULT_REDIS_URL,
    FACTORY_ACL_USER,
    allowlisted_clone_source,
    ensure_factory_acl_user,
    factory_ipc_url,
    redis_url_with_db,
    resolve_ephemeral_db,
    resolve_redis_url,
    validate_git_ref,
)
from megadesk_contracts.agent_audit import agent_audit_bind_args

log = logging.getLogger("pool")

IMAGE_NAME = os.environ.get("MACHINE_FACTORY_IMAGE", "machine-factory-agent:latest")
REDIS_IMAGE = os.environ.get("MACHINE_FACTORY_REDIS_IMAGE", "redis:7-alpine")
NETWORK_NAME = os.environ.get("MACHINE_FACTORY_NETWORK", "machine-factory-net")
DEFAULT_FACTORY_REDIS_URL = "redis://host.docker.internal:6379/0"
LOCAL_REDIS_URL = os.environ.get("REDIS_URL", DEFAULT_REDIS_URL)
_GH_AUTH_TIMEOUT_SEC = 10


def resolve_github_token() -> str:
    """Host token the sandbox needs to push and open a PR.

    Docker cannot use the host Git Credential Manager, so clone of a public
    repo succeeds while ``git push`` fails with "could not read Username".
    Prefer ``GH_TOKEN`` / ``GITHUB_TOKEN``, then the token from ``gh auth login``.
    """
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        token = (os.environ.get(name) or "").strip()
        if token:
            return token
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=_GH_AUTH_TIMEOUT_SEC,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return (result.stdout or "").strip()


# Labelled with its run key so a restarted manager can still find, follow and
# stop a sandbox it did not start. The alternative — remembering container names
# in memory — loses every live run the moment the BE is bounced.
RUN_KEY_LABEL = "megadesk.run_key"
REDIS_RUN_LABEL = "megadesk.redis_for"
CONTAINER_NAME_PREFIX = "mf-"
REDIS_NAME_PREFIX = "mf-redis-"
CONTAINER_SECRETS_DIR = "/run/megadesk-secrets"
CONTAINER_TOKEN_FILE = f"{CONTAINER_SECRETS_DIR}/github_token"
CONTAINER_ASKPASS = "/app/AgentHandler/git_askpass.py"
_SECRET_PATTERNS = (
    re.compile(r"(?i)(GH_TOKEN=|GITHUB_TOKEN=|CURSOR_API_KEY=)\S+"),
    re.compile(r"(://[^:@/\s]+:)[^@/\s]+@"),
    re.compile(r"(?i)(x-access-token:)[^@/\s]+"),
)


def redact_secrets(text: str, *secrets: str) -> str:
    """Strip tokens out of docker argv and error strings."""
    out = text or ""
    for secret in secrets:
        if secret:
            out = out.replace(secret, "***")
    for pattern in _SECRET_PATTERNS:
        if "://" in pattern.pattern:
            out = pattern.sub(r"\1***@", out)
        else:
            out = pattern.sub(r"\1***", out)
    return out


def secrets_dir_for(guid: str) -> Path:
    token = re.sub(r"[^a-zA-Z0-9_.-]", "-", (guid or "run").lower())[:48]
    return Path(tempfile.gettempdir()) / f"megadesk-mf-secrets-{token}"


def cleanup_sandbox_secrets(guid: str) -> None:
    path = secrets_dir_for(guid)
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)


def write_github_token_file(guid: str, token: str) -> Path:
    """Host-side 0600 token file, deleted when the sandbox is removed."""
    root = secrets_dir_for(guid)
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    root.mkdir(mode=0o700, parents=True)
    path = root / "github_token"
    path.write_text(token, encoding="utf-8")
    os.chmod(path, 0o600)
    os.chmod(root, 0o700)
    return root


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
        err = redact_secrets((exc.stderr or "").strip())
        out = redact_secrets((exc.stdout or "").strip())
        cmd = redact_secrets(" ".join(args))
        if err:
            log.error("docker %s failed (code=%s): %s", args[0], exc.returncode, err)
        if out:
            log.error("docker %s stdout: %s", args[0], out)
        raise RuntimeError(f"docker {args[0]} failed: {err or cmd}") from None


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


def factory_redis_url_for_container() -> str:
    """Host Redis as seen from inside a sandbox container (factory IPC bus)."""
    base = os.environ.get("REDIS_URL_CONTAINER", DEFAULT_FACTORY_REDIS_URL)
    # Prefer the process pair's ephemeral DB when the host REDIS_URL is set.
    host = os.environ.get("REDIS_URL", LOCAL_REDIS_URL)
    return redis_url_with_db(base, resolve_ephemeral_db(host))


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
    """
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


def redis_sidecar_name(guid: str) -> str:
    token = re.sub(r"[^a-zA-Z0-9_.-]", "-", guid.lower())[:48]
    return f"{REDIS_NAME_PREFIX}{token}"


def container_for_run(run_key: str) -> str:
    """The agent sandbox carrying this run key, or "" if none is left running."""
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


def redis_for_run(run_key: str) -> str:
    """The Redis sidecar for this run key, or "" if none remains."""
    result = _docker(
        [
            "ps",
            "-a",
            "--filter",
            f"label={REDIS_RUN_LABEL}={run_key}",
            "--format",
            "{{.Names}}",
        ],
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip().splitlines()[0].strip() if result.stdout.strip() else ""


def list_redis_sidecars() -> list[str]:
    """Guids of running Redis sidecars, from the ``megadesk.redis_for`` label."""
    result = _docker(
        [
            "ps",
            "--filter",
            f"label={REDIS_RUN_LABEL}",
            "--format",
            f'{{{{.Label "{REDIS_RUN_LABEL}"}}}}',
        ],
        check=False,
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]


def container_is_running(name: str) -> bool:
    result = _docker(["inspect", "-f", "{{.State.Running}}", name], check=False)
    return result.returncode == 0 and result.stdout.strip() == "true"


def remove_container(name: str) -> None:
    _docker(["rm", "-f", name], check=False)


def stop_redis_sidecar(run_key: str) -> None:
    """Remove the Redis sidecar for a finished or cancelled run."""
    name = redis_for_run(run_key)
    if name:
        log.info("Removing Redis sidecar %s for run %s", name, run_key)
        remove_container(name)
    cleanup_sandbox_secrets(run_key)


def _wait_redis_ready(name: str, *, timeout_sec: float = 20.0) -> None:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        result = _docker(
            ["exec", name, "redis-cli", "ping"],
            check=False,
        )
        if result.returncode == 0 and "PONG" in (result.stdout or "").upper():
            return
        time.sleep(0.25)
    raise RuntimeError(f"Redis sidecar {name} did not become ready")


def start_redis_sidecar(*, guid: str) -> str:
    """Start a per-run Redis container on the factory network. Returns its name."""
    ensure_network()
    name = redis_sidecar_name(guid)
    if _docker(["inspect", name], check=False).returncode == 0:
        log.info("Removing existing Redis sidecar %s before restart", name)
        remove_container(name)
    log.info("Starting Redis sidecar %s for run %s", name, guid)
    _docker(
        [
            "run",
            "-d",
            "--rm",
            "--name",
            name,
            "--network",
            NETWORK_NAME,
            "--label",
            f"{REDIS_RUN_LABEL}={guid}",
            REDIS_IMAGE,
        ]
    )
    _wait_redis_ready(name)
    return name


def start_ticket_sandbox(
    *,
    repo: str,
    ticket: str,
    repo_url: str,
    guid: str,
    ticket_id: str,
    auto_pr: bool = True,
    ref: str = "",
    api_key: str | None = None,
) -> str:
    """Start a Redis sidecar + AgentHandler sandbox that clones ``repo_url``.

    The agent MegaDesk uses the sidecar (``REDIS_URL``). Factory IPC
    (AGENTHANDLER / WORKORDER / FINISHED / GRAPHRUN) uses
    ``MEGADESK_FACTORY_REDIS_URL`` on the host pair. ``ref`` is the branch the
    sandbox clones and bases its pull request on; empty means
    ``DEFAULT_STARTING_REF``.
    Returns the agent container name.
    """
    key = api_key or os.environ.get("CURSOR_API_KEY")
    if not key:
        print("CURSOR_API_KEY is required to start sandboxes", file=sys.stderr)
        raise SystemExit(1)

    try:
        resolved_url, _name = allowlisted_clone_source(repo_url, allow_local=False)
        starting_ref = validate_git_ref(ref)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc

    gh_token = resolve_github_token()
    if auto_pr and not gh_token:
        raise RuntimeError(
            "GH_TOKEN, GITHUB_TOKEN, or `gh auth login` is required to push "
            "and open a PR from the sandbox"
        )

    acl_password = ensure_factory_acl_user(resolve_redis_url(LOCAL_REDIS_URL))
    factory_url = factory_ipc_url(factory_redis_url_for_container(), acl_password)
    secrets_root: Path | None = None
    if gh_token:
        secrets_root = write_github_token_file(guid, gh_token)

    ensure_network()
    redis_name = start_redis_sidecar(guid=guid)

    name = container_name(repo, ticket)
    if _docker(["inspect", "-f", "{{.State.Running}}", name], check=False).returncode == 0:
        log.info("Removing existing container %s before restart", name)
        remove_container(name)

    subject_url = f"redis://{redis_name}:6379/0"
    secret_args: list[str] = []
    if secrets_root is not None:
        secret_args = [
            "-v",
            f"{secrets_root}:{CONTAINER_SECRETS_DIR}:ro",
            "-e",
            f"MEGADESK_GITHUB_TOKEN_FILE={CONTAINER_TOKEN_FILE}",
            "-e",
            f"GIT_ASKPASS={CONTAINER_ASKPASS}",
        ]
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
        f"REPO_URL={resolved_url}",
        "-e",
        f"TICKET={ticket}",
        "-e",
        f"TICKET_ID={ticket_id}",
        "-e",
        f"AUTO_PR={'true' if auto_pr else 'false'}",
        "-e",
        "WORKSPACE=/workspace",
        "-e",
        f"STARTING_REF={starting_ref}",
        "-e",
        "GIT_TERMINAL_PROMPT=0",
        *secret_args,
        *agent_audit_bind_args(guid),
        IMAGE_NAME,
    ]
    log.info(
        "Starting sandbox %s clone=%s ref=%s guid=%s ticket_id=%s redis=%s github_auth=%s factory_user=%s",
        name,
        resolved_url,
        starting_ref,
        guid,
        ticket_id,
        redis_name,
        "present" if gh_token else "absent",
        FACTORY_ACL_USER,
    )
    try:
        _docker(args)
    except Exception:
        stop_redis_sidecar(guid)
        raise
    _follow_container_logs(name)
    return name
