"""Local git merge operations for MergeManager."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class MergeOutcome(str, Enum):
    SUCCESS = "success"
    CONFLICTS = "conflicts"
    DIRTY_AGENTS = "dirty_agents"
    ERROR = "error"


@dataclass
class MergeResult:
    outcome: MergeOutcome
    message: str = ""
    branch: str = ""


def _run(
    cmd: list[str],
    *,
    cwd: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        check=False,
        text=True,
        capture_output=True,
    )


def git_remote_url(worktree: Path) -> str:
    """Return origin URL for a worktree, or empty string."""
    result = _run(["git", "remote", "get-url", "origin"], cwd=worktree)
    if result.returncode != 0:
        return ""
    return (result.stdout or "").strip()


def current_branch(worktree: Path) -> str:
    result = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=worktree)
    if result.returncode != 0:
        raise RuntimeError(
            f"Could not read branch at {worktree}: {result.stderr.strip()}"
        )
    branch = (result.stdout or "").strip()
    if not branch or branch == "HEAD":
        raise RuntimeError(f"Detached HEAD at {worktree}")
    return branch


def is_dirty(worktree: Path) -> bool:
    result = _run(["git", "status", "--porcelain"], cwd=worktree)
    if result.returncode != 0:
        raise RuntimeError(
            f"git status failed at {worktree}: {result.stderr.strip()}"
        )
    return bool((result.stdout or "").strip())


def hard_reset_agents(agent_dir: Path) -> None:
    """Hard-reset the agents worktree to a clean state."""
    if not agent_dir.is_dir():
        raise FileNotFoundError(f"agents worktree missing: {agent_dir}")
    reset = _run(["git", "reset", "--hard", "HEAD"], cwd=agent_dir)
    if reset.returncode != 0:
        raise RuntimeError(
            f"git reset --hard failed: {reset.stderr.strip() or reset.stdout}"
        )
    clean = _run(["git", "clean", "-fd"], cwd=agent_dir)
    if clean.returncode != 0:
        raise RuntimeError(
            f"git clean -fd failed: {clean.stderr.strip() or clean.stdout}"
        )


def push_agents(agent_dir: Path, *, branch: str | None = None) -> str:
    """Push the agents worktree branch to origin. Returns the branch name."""
    if not agent_dir.is_dir():
        raise FileNotFoundError(f"agents worktree missing: {agent_dir}")
    target = branch or current_branch(agent_dir)
    push = _run(["git", "push", "-u", "origin", target], cwd=agent_dir)
    if push.returncode != 0:
        detail = (push.stderr or push.stdout or "git push failed").strip()
        raise RuntimeError(f"git push origin {target} failed: {detail}")
    return target


def attempt_merge(*, wt: Path, agent_dir: Path) -> MergeResult:
    """Try merging the ticket worktree branch into agents, then push agents.

    Returns DIRTY_AGENTS without attempting merge when agents has local changes.
    On conflicts, aborts the merge and returns CONFLICTS.
    On successful local merge, always pushes the agents worktree to origin.
    """
    if not wt.is_dir():
        return MergeResult(MergeOutcome.ERROR, f"Ticket worktree missing: {wt}")
    if not agent_dir.is_dir():
        return MergeResult(
            MergeOutcome.ERROR, f"Agents worktree missing: {agent_dir}"
        )

    try:
        if is_dirty(agent_dir):
            return MergeResult(
                MergeOutcome.DIRTY_AGENTS,
                "agents worktree has uncommitted changes",
            )
        branch = current_branch(wt)
        agents_branch = current_branch(agent_dir)
    except RuntimeError as exc:
        return MergeResult(MergeOutcome.ERROR, str(exc))

    merge = _run(["git", "merge", "--no-ff", branch], cwd=agent_dir)
    if merge.returncode == 0:
        try:
            pushed = push_agents(agent_dir, branch=agents_branch)
        except (OSError, RuntimeError) as exc:
            return MergeResult(
                MergeOutcome.ERROR,
                f"Merged {branch} into agents, but push failed: {exc}",
                branch=branch,
            )
        return MergeResult(
            MergeOutcome.SUCCESS,
            f"Merged {branch} into agents and pushed {pushed}",
            branch=branch,
        )

    combined = f"{merge.stdout}\n{merge.stderr}".lower()
    if "conflict" in combined:
        abort = _run(["git", "merge", "--abort"], cwd=agent_dir)
        abort_note = ""
        if abort.returncode != 0:
            abort_note = f" (merge --abort failed: {abort.stderr.strip()})"
        return MergeResult(
            MergeOutcome.CONFLICTS,
            f"Merge conflicts on {branch}{abort_note}",
            branch=branch,
        )

    return MergeResult(
        MergeOutcome.ERROR,
        (merge.stderr or merge.stdout or "git merge failed").strip(),
        branch=branch,
    )
