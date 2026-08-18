"""Validate repos, create bare clones and worktrees under Floor/."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger("floor")

REQUIRED_BRANCHES = ("main", "dev", "agents")
TICKET_BRANCH_PREFIX = "ticket/"
# Ticket worktrees MUST always be created from this branch — never main/dev/HEAD.
TICKET_BASE_BRANCH = "agents"


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def default_floor() -> Path:
    """Fixed Floor root: /Floor when present or on non-Windows, else ./Floor."""
    unix_floor = Path("/Floor")
    if unix_floor.exists() or os.name != "nt":
        return Path(os.environ.get("FLOOR_ROOT", "/Floor"))
    return Path(os.environ.get("FLOOR_ROOT", project_root() / "Floor"))


def repo_name_from_url(url: str) -> str:
    """Derive a Floor folder name from a git URL or bare repo identifier."""
    text = url.strip()
    if re.match(r"^[\w.-]+$", text) and "://" not in text and "/" not in text:
        return text
    path = urlparse(text).path.rstrip("/")
    name = path.rsplit("/", 1)[-1]
    if name.endswith(".git"):
        name = name[: -len(".git")]
    if not name or not re.match(r"^[\w.-]+$", name):
        raise ValueError(f"Could not derive a safe repo name from URL: {url}")
    return name


def safe_repo_name(name: str) -> str:
    """Sanitize REPO name for Floor folder use."""
    cleaned = name.strip().replace(" ", "-")
    if not cleaned or not re.match(r"^[\w.-]+$", cleaned):
        raise ValueError(f"Invalid repo name: {name!r}")
    return cleaned


def repo_dir(repo: str, floor_root: Path | None = None) -> Path:
    floor_root = floor_root or default_floor()
    return floor_root / safe_repo_name(repo)


def agents_worktree(repo: str, floor_root: Path | None = None) -> Path:
    return repo_dir(repo, floor_root) / "wt" / "agents"


def floor_repo_ready(repo: str, floor_root: Path | None = None) -> bool:
    """True when Floor/<name>/.bare already exists."""
    return (repo_dir(repo, floor_root) / ".bare").exists()


def safe_ticket_name(name: str) -> str:
    """Sanitize ticket name for filesystem / branch use."""
    cleaned = name.strip().replace(" ", "-")
    if not cleaned or not re.match(r"^[\w.-]+$", cleaned):
        raise ValueError(f"Invalid ticket name: {name!r}")
    return cleaned


def ticket_worktree(
    repo: str,
    ticket_name: str,
    floor_root: Path | None = None,
) -> Path:
    """Where a ticket's worktree belongs, whether or not it exists yet."""
    return repo_dir(repo, floor_root) / "wt" / "tickets" / safe_ticket_name(ticket_name)


def _run(cmd: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        check=True,
        text=True,
        capture_output=True,
    )


def remote_branches(url: str) -> set[str]:
    result = _run(["git", "ls-remote", "--heads", url])
    branches: set[str] = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        ref = parts[1]
        prefix = "refs/heads/"
        if ref.startswith(prefix):
            branches.add(ref[len(prefix) :])
    return branches


def validate_repo(url: str) -> None:
    branches = remote_branches(url)
    missing = [b for b in REQUIRED_BRANCHES if b not in branches]
    if missing:
        raise ValueError(
            f"Repo {url} is invalid for this setup: missing branch(es) "
            f"{', '.join(missing)}. Required: {', '.join(REQUIRED_BRANCHES)}"
        )


def _ensure_worktree(bare_dir: Path, path: Path, branch: str) -> None:
    if path.exists() and (path / ".git").exists():
        return
    if path.exists():
        shutil.rmtree(path)
    _run(["git", "worktree", "add", str(path), branch], cwd=bare_dir)


def ensure_repo(
    *,
    repo: str,
    url: str,
    floor_root: Path | None = None,
) -> Path:
    """Ensure Floor/<repo> exists, cloning from ``url`` when missing.

    If .bare already exists, refresh worktrees. Otherwise validate that the
    remote has main/dev/agents and clone. Rejects invalid remotes.
    """
    floor_root = floor_root or default_floor()
    name = safe_repo_name(repo)
    existing = floor_root / name
    if (existing / ".bare").exists():
        return setup_repo(url, floor_root, name=name)
    if not url or not str(url).strip():
        raise ValueError(
            f"Repo {name!r} is not under Floor at {existing} and no URL was provided."
        )
    return setup_repo(url.strip(), floor_root, name=name)


def setup_repo(
    url: str,
    floor_root: Path | None = None,
    *,
    name: str | None = None,
) -> Path:
    """Create Floor/<repo>/.bare and wt/{dev,agents,tickets}. Returns repo dir."""
    floor_root = floor_root or default_floor()
    folder = safe_repo_name(name) if name else repo_name_from_url(url)
    dest = floor_root / folder
    bare_dir = dest / ".bare"
    wt_dev = dest / "wt" / "dev"
    wt_agents = dest / "wt" / "agents"
    wt_tickets = dest / "wt" / "tickets"

    floor_root.mkdir(parents=True, exist_ok=True)

    if bare_dir.exists():
        log.info("%s: .bare already exists, refreshing worktrees if needed", folder)
        (dest / "wt").mkdir(parents=True, exist_ok=True)
        wt_tickets.mkdir(parents=True, exist_ok=True)
        _ensure_worktree(bare_dir, wt_dev, "dev")
        _ensure_worktree(bare_dir, wt_agents, "agents")
        return dest

    validate_repo(url)

    dest.mkdir(parents=True, exist_ok=True)
    (dest / "wt").mkdir(parents=True, exist_ok=True)
    wt_tickets.mkdir(parents=True, exist_ok=True)

    log.info("%s: cloning bare repo -> %s", folder, bare_dir)
    _run(["git", "clone", "--bare", url, str(bare_dir)])

    log.info("%s: creating worktree wt/dev (branch dev)", folder)
    _run(["git", "worktree", "add", str(wt_dev), "dev"], cwd=bare_dir)
    log.info("%s: creating worktree wt/agents (branch agents)", folder)
    _run(["git", "worktree", "add", str(wt_agents), "agents"], cwd=bare_dir)

    return dest


def create_ticket_worktree(
    repo: str,
    ticket_name: str,
    floor_root: Path | None = None,
) -> Path:
    """Create Floor/<repo>/wt/tickets/<name> as branch ticket/<name> from agents.

    New ticket branches are ALWAYS forked from ``TICKET_BASE_BRANCH`` (agents),
    never from main, dev, or bare HEAD. Requires the repo already prepared under
    Floor. Returns the ticket worktree path.
    """
    floor_root = floor_root or default_floor()
    name = safe_repo_name(repo)
    ticket = safe_ticket_name(ticket_name)
    dest = floor_root / name
    bare_dir = dest / ".bare"
    tickets_dir = dest / "wt" / "tickets"
    ticket_path = ticket_worktree(name, ticket, floor_root)
    branch = f"{TICKET_BRANCH_PREFIX}{ticket}"

    if not bare_dir.exists():
        raise FileNotFoundError(
            f"Repo {name} has no Floor worktree at {dest}. "
            "Ensure the repo exists under Floor (WORKORDER provides URL to create it)."
        )

    tickets_dir.mkdir(parents=True, exist_ok=True)

    if ticket_path.exists() and (ticket_path / ".git").exists():
        log.info("%s: ticket worktree already exists at %s", name, ticket_path)
        return ticket_path

    if ticket_path.exists():
        shutil.rmtree(ticket_path)

    local_refs = _run(["git", "show-ref", "--heads"], cwd=bare_dir)
    heads = local_refs.stdout
    if f"refs/heads/{TICKET_BASE_BRANCH}" not in heads:
        raise FileNotFoundError(
            f"Repo {name} has no local '{TICKET_BASE_BRANCH}' branch; "
            "cannot create a ticket worktree."
        )

    # If the branch already exists (orphan path), attach worktree to it; else
    # create a new branch starting at agents (never main/dev/HEAD).
    branch_exists = f"refs/heads/{branch}" in heads
    if branch_exists:
        log.info("%s: attaching worktree %s to existing branch %s", name, ticket_path, branch)
        _run(["git", "worktree", "add", str(ticket_path), branch], cwd=bare_dir)
    else:
        log.info(
            "%s: creating worktree %s (new branch %s from %s)",
            name,
            ticket_path,
            branch,
            TICKET_BASE_BRANCH,
        )
        _run(
            [
                "git",
                "worktree",
                "add",
                "-b",
                branch,
                str(ticket_path),
                TICKET_BASE_BRANCH,
            ],
            cwd=bare_dir,
        )

    return ticket_path

