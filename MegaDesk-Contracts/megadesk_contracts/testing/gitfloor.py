"""A real local git Floor for tests.

This builds the actual layout MachineFactory produces::

    <root>/origin.git                          bare, stands in for GitHub
    <root>/Floor/<repo>/.bare                  bare clone of origin
    <root>/Floor/<repo>/wt/dev                 worktree on 'dev'
    <root>/Floor/<repo>/wt/tickets/<ticket>    worktree on 'ticket/<ticket>'

The pushable local ``origin`` matters: a successful merge *always* pushes, so
without one the success path would report ERROR and a test would assert the
wrong thing.

Repos only need a ``dev`` branch — the branch both factories start work from.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence

from megadesk_contracts.wire.factory import DEFAULT_STARTING_REF

REQUIRED_BRANCHES = (DEFAULT_STARTING_REF,)
TICKET_BRANCH_PREFIX = "ticket/"

_IDENTITY = (
    "-c",
    "user.name=MegaDesk Test",
    "-c",
    "user.email=test@megadesk.invalid",
    "-c",
    "commit.gpgsign=false",
)


class GitError(RuntimeError):
    pass


def git(
    *args: str,
    cwd: Path,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *_IDENTITY, *args],
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        raise GitError(
            f"git {' '.join(args)} failed in {cwd} (exit {result.returncode})\n"
            f"stdout: {result.stdout.strip()}\nstderr: {result.stderr.strip()}"
        )
    return result


def _force_rmtree(path: Path) -> None:
    """Remove a tree containing git's read-only object files (Windows)."""

    def _retry(func, target, _exc):  # pragma: no cover - platform dependent
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except Exception:
            pass

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_retry)
    else:
        shutil.rmtree(path, onerror=_retry)


class GitFloor:
    """Builds and inspects a real git Floor under ``root``."""

    def __init__(self, root: Path, repo: str = "widgets") -> None:
        self.root = Path(root)
        self.repo = repo
        self.origin = self.root / "origin.git"
        self.floor = self.root / "Floor"

    # --- layout ---

    @property
    def repo_dir(self) -> Path:
        return self.floor / self.repo

    @property
    def bare_dir(self) -> Path:
        return self.repo_dir / ".bare"

    @property
    def dev_dir(self) -> Path:
        return self.repo_dir / "wt" / DEFAULT_STARTING_REF

    @property
    def tickets_dir(self) -> Path:
        return self.repo_dir / "wt" / "tickets"

    def ticket_dir(self, ticket_name: str) -> Path:
        return self.tickets_dir / ticket_name

    @staticmethod
    def ticket_branch(ticket_name: str) -> str:
        return f"{TICKET_BRANCH_PREFIX}{ticket_name}"

    # --- construction ---

    def create(self) -> "GitFloor":
        """Build ``origin.git`` with a ``dev`` branch, then clone it into Floor."""
        self.root.mkdir(parents=True, exist_ok=True)
        seed = self.root / "seed"
        seed.mkdir(parents=True, exist_ok=True)

        git("init", "-b", DEFAULT_STARTING_REF, ".", cwd=seed)
        (seed / "README.md").write_text("seed repo\n", encoding="utf-8")
        git("add", "README.md", cwd=seed)
        git("commit", "-m", "seed: initial commit", cwd=seed)

        git("clone", "--bare", str(seed), str(self.origin), cwd=self.root)

        self.floor.mkdir(parents=True, exist_ok=True)
        self.repo_dir.mkdir(parents=True, exist_ok=True)
        git("clone", "--bare", str(self.origin), str(self.bare_dir), cwd=self.root)
        self.tickets_dir.mkdir(parents=True, exist_ok=True)
        git("worktree", "add", str(self.dev_dir), DEFAULT_STARTING_REF, cwd=self.bare_dir)
        return self

    def add_ticket(self, ticket_name: str) -> Path:
        """Create ``wt/tickets/<name>`` on ``ticket/<name>``, forked from ``dev``."""
        path = self.ticket_dir(ticket_name)
        self.tickets_dir.mkdir(parents=True, exist_ok=True)
        git(
            "worktree",
            "add",
            "-b",
            self.ticket_branch(ticket_name),
            str(path),
            DEFAULT_STARTING_REF,
            cwd=self.bare_dir,
        )
        return path

    def destroy(self) -> None:
        if self.root.exists():
            _force_rmtree(self.root)

    # --- authoring ---

    def commit(
        self,
        worktree: Path,
        relpath: str,
        text: str,
        message: Optional[str] = None,
    ) -> str:
        """Write a file in ``worktree`` and commit it. Returns the new sha."""
        target = Path(worktree) / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        git("add", "--", relpath, cwd=Path(worktree))
        git("commit", "-m", message or f"touch {relpath}", cwd=Path(worktree))
        return self.head(worktree)

    def dirty(self, worktree: Path, relpath: str = "scratch.txt") -> None:
        """Leave an uncommitted change behind."""
        (Path(worktree) / relpath).write_text("uncommitted\n", encoding="utf-8")

    def make_conflict(
        self,
        ticket_name: str,
        *,
        relpath: str = "conflict.txt",
    ) -> Path:
        """Commit divergent content for the same file on ``dev`` and the ticket.

        The ticket worktree must already exist, otherwise its branch would start
        from the ``dev`` commit and the merge would fast-forward cleanly.
        """
        ticket_path = self.ticket_dir(ticket_name)
        if not ticket_path.is_dir():
            raise GitError(
                f"ticket worktree {ticket_path} does not exist; call add_ticket first"
            )
        self.commit(ticket_path, relpath, "ticket version\n", "ticket: edit shared file")
        self.commit(self.dev_dir, relpath, "dev version\n", "dev: edit shared file")
        return ticket_path

    # --- inspection ---

    def head(self, worktree: Path, ref: str = "HEAD") -> str:
        return git("rev-parse", ref, cwd=Path(worktree)).stdout.strip()

    def origin_sha(self, branch: str) -> str:
        result = git(
            "rev-parse", f"refs/heads/{branch}", cwd=self.origin, check=False
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    def branch_sha(self, branch: str) -> str:
        result = git(
            "rev-parse", f"refs/heads/{branch}", cwd=self.bare_dir, check=False
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    def current_branch(self, worktree: Path) -> str:
        return git("rev-parse", "--abbrev-ref", "HEAD", cwd=Path(worktree)).stdout.strip()

    def is_clean(self, worktree: Path) -> bool:
        return not git("status", "--porcelain", cwd=Path(worktree)).stdout.strip()

    def merge_in_progress(self, worktree: Path) -> bool:
        result = git(
            "rev-parse", "--verify", "--quiet", "MERGE_HEAD", cwd=Path(worktree), check=False
        )
        return result.returncode == 0

    def contains(self, worktree: Path, sha: str) -> bool:
        """True when ``sha`` is an ancestor of the worktree's HEAD."""
        result = git(
            "merge-base", "--is-ancestor", sha, "HEAD", cwd=Path(worktree), check=False
        )
        return result.returncode == 0

    def subjects(self, worktree: Path, count: int = 10) -> Sequence[str]:
        result = git(
            "log", f"-{count}", "--pretty=%s", cwd=Path(worktree), check=False
        )
        return [line for line in result.stdout.splitlines() if line]
