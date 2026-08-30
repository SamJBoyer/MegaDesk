"""Clone the target repo into the sandbox workspace and open a PR when done.

Replaces the old Floor worktree mount + gitdir pointer rewrite. The sandbox owns
a plain clone under ``WORKSPACE``; factory IPC stays on the host Redis bus.

GitHub credentials come from ``MEGADESK_GITHUB_TOKEN_FILE`` + ``GIT_ASKPASS``,
never from a token embedded in the remote URL.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Optional

from megadesk_contracts.repo import allowlisted_clone_source, validate_git_ref
from megadesk_contracts.wire.factory import DEFAULT_STARTING_REF

log = logging.getLogger("agent_handler.repo_clone")

_GIT_TIMEOUT_SEC = 300
_DEFAULT_REF = DEFAULT_STARTING_REF
_ASKPASS_MODULE = str(Path(__file__).with_name("git_askpass.py"))
_SECRET_PATTERNS = (
    re.compile(r"(?i)(GH_TOKEN=|GITHUB_TOKEN=|CURSOR_API_KEY=)\S+"),
    re.compile(r"(://[^:@/\s]+:)[^@/\s]+@"),
    re.compile(r"(?i)(x-access-token:)[^@/\s]+"),
)


def redact_secrets(text: str, *secrets: str) -> str:
    """Strip planted tokens out of git/docker error strings."""
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


def _github_token() -> str:
    path = (os.environ.get("MEGADESK_GITHUB_TOKEN_FILE") or "").strip()
    if path:
        try:
            token = Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            token = ""
        if token:
            return token
    return (
        os.environ.get("GH_TOKEN")
        or os.environ.get("GITHUB_TOKEN")
        or ""
    ).strip()


def _auth_url(url: str) -> str:
    """Return the clone URL unchanged. Tokens must not be interpolated here."""
    return (url or "").strip()


def _git_env() -> dict[str, str]:
    env: dict[str, str] = {"GIT_TERMINAL_PROMPT": "0"}
    token_file = (os.environ.get("MEGADESK_GITHUB_TOKEN_FILE") or "").strip()
    if token_file:
        env["MEGADESK_GITHUB_TOKEN_FILE"] = token_file
        env["GIT_ASKPASS"] = os.environ.get("GIT_ASKPASS") or _ASKPASS_MODULE
        env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _gh_env() -> dict[str, str]:
    env = _git_env()
    token = _github_token()
    if token:
        env["GH_TOKEN"] = token
    return env


def _run(
    args: list[str],
    *,
    cwd: Optional[Path] = None,
    check: bool = True,
    env: Optional[dict[str, str]] = None,
) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    merged.setdefault("GIT_TERMINAL_PROMPT", "0")
    merged.update(_git_env())
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
        token = _github_token()
        safe_args = redact_secrets(" ".join(args), token)
        raise RuntimeError(f"{safe_args} failed: {redact_secrets(err, token)}")
    return result


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
        text = (repo_url or "").strip()
        self.repo_url = (
            allowlisted_clone_source(text, allow_local=False)[0] if text else ""
        )
        self.ticket = (ticket or "").strip()
        self.starting_ref = validate_git_ref(starting_ref)
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
        if not _github_token():
            raise RuntimeError(
                "GH_TOKEN is not set in the sandbox; cannot push or open a PR"
            )
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
            env=_gh_env(),
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
                env=_gh_env(),
            )
            text = (listed.stdout or "").strip()
            if listed.returncode != 0 or not text:
                token = _github_token()
                detail = redact_secrets(created.stderr or created.stdout or "", token)
                raise RuntimeError(f"gh pr create failed: {detail}")
        # gh pr create prints the URL on success.
        match = re.search(r"https://github\.com/[^\s]+/pull/\d+", text)
        self.pr_url = match.group(0) if match else text.splitlines()[-1].strip()
        log.info("Opened pull request %s", self.pr_url)
        return self.pr_url
