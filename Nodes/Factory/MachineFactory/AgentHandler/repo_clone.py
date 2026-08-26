"""Clone the target repo into the sandbox workspace and open a PR when done.

Replaces the old Floor worktree mount + gitdir pointer rewrite. The sandbox owns
a plain clone under ``WORKSPACE``; factory IPC stays on the host Redis bus.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, urlunparse

log = logging.getLogger("agent_handler.repo_clone")

_GIT_TIMEOUT_SEC = 300
_DEFAULT_REF = "agents"


def _run(
    args: list[str],
    *,
    cwd: Optional[Path] = None,
    check: bool = True,
    env: Optional[dict[str, str]] = None,
) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    result = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_SEC,
        check=False,
        env=merged,
    )
    if check and result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"{' '.join(args)} failed: {err}")
    return result


def _auth_url(url: str) -> str:
    """Embed GH_TOKEN into an https URL when present so clone/push can auth."""
    token = (
        os.environ.get("GH_TOKEN")
        or os.environ.get("GITHUB_TOKEN")
        or ""
    ).strip()
    if not token or not url.startswith("https://"):
        return url
    parsed = urlparse(url)
    if parsed.username:
        return url
    netloc = f"x-access-token:{token}@{parsed.hostname}"
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    return urlunparse(parsed._replace(netloc=netloc))


def ticket_branch(ticket: str) -> str:
    cleaned = re.sub(r"[^\w.-]+", "-", (ticket or "").strip()).strip("-") or "work"
    return f"ticket/{cleaned}"


class SandboxRepo:
    """Clone, branch, push, and open a PR inside the sandbox workspace."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        repo_url: str,
        ticket: str,
        starting_ref: str = _DEFAULT_REF,
        auto_pr: bool = True,
    ) -> None:
        self.workspace = Path(workspace)
        self.repo_url = (repo_url or "").strip()
        self.ticket = (ticket or "").strip()
        self.starting_ref = (starting_ref or _DEFAULT_REF).strip() or _DEFAULT_REF
        self.auto_pr = bool(auto_pr)
        self.branch = ticket_branch(self.ticket)
        self.pr_url = ""

    def prepare(self) -> None:
        if not self.repo_url:
            raise RuntimeError("REPO_URL is required to clone into the sandbox")
        self.workspace.mkdir(parents=True, exist_ok=True)
        git_dir = self.workspace / ".git"
        if git_dir.exists():
            log.info("Workspace already has a git repo at %s", self.workspace)
        else:
            # Clone into a temp sibling then move contents — WORKSPACE may exist empty.
            parent = self.workspace.parent
            staging = parent / f".clone-{os.getpid()}"
            if staging.exists():
                _run(["rm", "-rf", str(staging)], check=False)
            log.info(
                "Cloning %s (ref=%s) into %s",
                self.repo_url,
                self.starting_ref,
                self.workspace,
            )
            _run(
                [
                    "git",
                    "clone",
                    "--branch",
                    self.starting_ref,
                    _auth_url(self.repo_url),
                    str(staging),
                ]
            )
            for child in staging.iterdir():
                dest = self.workspace / child.name
                if dest.exists():
                    _run(["rm", "-rf", str(dest)], check=False)
                child.rename(dest)
            _run(["rm", "-rf", str(staging)], check=False)

        _run(["git", "fetch", "origin", self.starting_ref], cwd=self.workspace, check=False)
        # Detach any existing branch of the same name, then create/reset ours.
        existing = _run(
            ["git", "show-ref", "--verify", f"refs/heads/{self.branch}"],
            cwd=self.workspace,
            check=False,
        )
        if existing.returncode == 0:
            _run(["git", "checkout", self.branch], cwd=self.workspace)
            _run(
                ["git", "reset", "--hard", f"origin/{self.starting_ref}"],
                cwd=self.workspace,
                check=False,
            )
        else:
            _run(
                ["git", "checkout", "-B", self.branch, f"origin/{self.starting_ref}"],
                cwd=self.workspace,
                check=False,
            )
            # Fallback when origin/<ref> is missing (local-only clone).
            head = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=self.workspace)
            if head.stdout.strip() != self.branch:
                _run(
                    ["git", "checkout", "-B", self.branch, self.starting_ref],
                    cwd=self.workspace,
                )
        log.info("Sandbox repo ready on branch %s", self.branch)

    def restore(self) -> None:
        """No host gitdir pointers to restore; kept for call-site symmetry."""
        return

    def publish_branch(self) -> str:
        """Push the ticket branch and optionally open a PR. Returns pr_url."""
        remote = _auth_url(self.repo_url)
        _run(["git", "remote", "set-url", "origin", remote], cwd=self.workspace)
        _run(["git", "push", "-u", "origin", f"HEAD:{self.branch}"], cwd=self.workspace)
        if not self.auto_pr:
            self.pr_url = ""
            return ""
        title = f"ticket/{self.ticket}" if self.ticket else self.branch
        created = _run(
            [
                "gh",
                "pr",
                "create",
                "--base",
                self.starting_ref,
                "--head",
                self.branch,
                "--title",
                title,
                "--body",
                f"MachineFactory sandbox work for {self.ticket or self.branch}.",
            ],
            cwd=self.workspace,
            check=False,
        )
        text = (created.stdout or created.stderr or "").strip()
        if created.returncode != 0:
            # PR may already exist — fall back to listing it.
            listed = _run(
                [
                    "gh",
                    "pr",
                    "list",
                    "--head",
                    self.branch,
                    "--json",
                    "url",
                    "--jq",
                    ".[0].url",
                ],
                cwd=self.workspace,
                check=False,
            )
            text = (listed.stdout or "").strip()
            if listed.returncode != 0 or not text:
                raise RuntimeError(f"gh pr create failed: {created.stderr or created.stdout}")
        # gh pr create prints the URL on success.
        match = re.search(r"https://github\.com/[^\s]+/pull/\d+", text)
        self.pr_url = match.group(0) if match else text.splitlines()[-1].strip()
        log.info("Opened pull request %s", self.pr_url)
        return self.pr_url
