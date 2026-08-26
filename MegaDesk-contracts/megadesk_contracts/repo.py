"""Read-only repo clones for nodes that only need to look at code.

MachineFactory agents write inside a Docker sandbox clone and open a PR; they do
not share a host worktree. A node that only answers questions about code needs a
plain clone with a refresh that throws local changes away — sharing a tree with a
writing agent would race it.

``safe_repo_name`` here is the shared sanitizer (also used by MachineFactory).
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

DEFAULT_SCOPE_DIRNAME = "Scope"
# Shallow by default: answering questions about code as it stands now does not
# need history, and a deep clone of a large repo is a slow first question.
# Pass ``depth=None`` when the point is "why did this change".
DEFAULT_DEPTH: Optional[int] = 1
GIT_TIMEOUT_SEC = 300


class CloneError(RuntimeError):
    """A git command failed; the message carries git's own stderr."""


def default_scope_root() -> Path:
    """``SCOPE_ROOT`` if set, else ``./Scope`` under the current directory."""
    configured = (os.environ.get("SCOPE_ROOT") or "").strip()
    if configured:
        return Path(configured)
    return Path.cwd() / DEFAULT_SCOPE_DIRNAME


def safe_repo_name(name: str) -> str:
    cleaned = str(name).strip().replace(" ", "-")
    if not cleaned or not re.match(r"^[\w.-]+$", cleaned):
        raise ValueError(f"Invalid repo name: {name!r}")
    return cleaned


def repo_name_from_url(url: str) -> str:
    """Derive a directory name from anything git can clone.

    Handles https, SSH shorthand, a bare name, and local paths in either slash
    direction — the last of which is what the integration suite clones from, and
    what ``urlparse`` mangles on Windows by reading ``C:`` as a URL scheme.
    """
    text = str(url).strip().replace("\\", "/")
    if not text:
        raise ValueError("Cannot derive a repo name from an empty URL")

    if re.match(r"^[\w.-]+$", text):
        return _strip_dot_git(text)
    if text.startswith("git@"):
        text = text.split(":", 1)[-1]
    elif "://" in text:
        text = urlparse(text).path

    name = _strip_dot_git(text.rstrip("/").rsplit("/", 1)[-1])
    if not name or not re.match(r"^[\w.-]+$", name):
        raise ValueError(f"Could not derive a safe repo name from URL: {url}")
    return name


def _strip_dot_git(name: str) -> str:
    return name[: -len(".git")] if name.endswith(".git") else name


def _git(args: list[str], *, cwd: Optional[Path] = None) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SEC,
            check=False,
        )
    except FileNotFoundError as exc:
        raise CloneError("git not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise CloneError(f"git {' '.join(args)} timed out") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise CloneError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout


def is_clone(path: Path) -> bool:
    return Path(path).is_dir() and (Path(path) / ".git").exists()


def clone_path(url: str, root: Optional[Path] = None, *, name: Optional[str] = None) -> Path:
    root = Path(root) if root is not None else default_scope_root()
    folder = safe_repo_name(name) if name else repo_name_from_url(url)
    return root / folder


def ensure_clone(
    *,
    url: str,
    root: Optional[Path] = None,
    name: Optional[str] = None,
    depth: Optional[int] = DEFAULT_DEPTH,
) -> Path:
    """Clone ``url`` under ``root`` if it is not there already, and return the path.

    Idempotent: an existing clone is returned untouched, since re-cloning would
    throw away a fetch the caller may have just paid for. Refreshing is a
    separate, explicitly destructive call.
    """
    dest = clone_path(url, root, name=name)
    if is_clone(dest):
        return dest
    if dest.exists() and any(dest.iterdir()):
        raise ValueError(
            f"{dest} already exists and is not a git clone; refusing to overwrite it"
        )
    if not str(url).strip():
        raise ValueError("ensure_clone requires a URL when the clone does not exist yet")

    dest.parent.mkdir(parents=True, exist_ok=True)
    args = ["clone"]
    if depth and int(depth) > 0:
        args += ["--depth", str(int(depth))]
    args += [str(url).strip(), str(dest)]
    _git(args)
    return dest


def refresh_clone(path: Path) -> str:
    """Fetch and hard-reset the clone to its remote default branch.

    Destructive on purpose: this clone exists to be read, so local edits are
    either a mistake or an agent that ignored its instructions. Returns the new
    HEAD sha.
    """
    path = Path(path)
    if not is_clone(path):
        raise ValueError(f"{path} is not a git clone")
    _git(["fetch", "--prune", "origin"], cwd=path)
    _git(["reset", "--hard", remote_default_ref(path)], cwd=path)
    _git(["clean", "-fd"], cwd=path)
    return head_sha(path)


def remote_default_ref(path: Path) -> str:
    """``origin/<default branch>``, falling back to the tracked upstream."""
    path = Path(path)
    try:
        ref = _git(
            ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], cwd=path
        ).strip()
        if ref:
            return ref
    except CloneError:
        pass
    try:
        _git(["remote", "set-head", "origin", "--auto"], cwd=path)
        ref = _git(
            ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], cwd=path
        ).strip()
        if ref:
            return ref
    except CloneError:
        pass
    return _git(["rev-parse", "--abbrev-ref", "@{upstream}"], cwd=path).strip()


def remote_url(path: Path, remote: str = "origin") -> str:
    """The clone's remote URL.

    This is how a node that only has a local clone recovers the address a cloud
    agent needs: the agent clones from the remote itself and never sees the local
    copy, so the remote is the only shared reference.
    """
    return _git(["remote", "get-url", remote], cwd=Path(path)).strip()


def head_sha(path: Path) -> str:
    return _git(["rev-parse", "HEAD"], cwd=Path(path)).strip()


def current_branch(path: Path) -> str:
    return _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=Path(path)).strip()
