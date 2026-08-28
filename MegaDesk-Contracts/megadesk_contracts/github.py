"""GitHub URL parse, ``gh`` CLI, and labeled-issue poll.

TicketDispatcher and PRManager both take a pasted GitHub URL, check that the
remote exists, and poll ``gh issue list`` for a label. CloudFactory and
CodeScope need the same URL canonicalization so ``owner/repo``, HTTPS, and SSH
forms identify one clone. Those copies used to live in each node and were free
to drift — ``WORKORDER`` taught us not to do that.

``run_gh`` is the seam tests cut: ``FakeGh`` replaces it so poll loops run
without network or auth.
"""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from megadesk_contracts.repo import repo_name_from_url

GH_TIMEOUT_SEC = 15
ISSUE_JSON_FIELDS = "number,title,body"
EMPTY_GITHUB_URL = "Enter a GitHub repository URL"
UNSUPPORTED_GITHUB_URL = "Unsupported URL (GitHub https or SSH required)"

_GITHUB_SSH = re.compile(
    r"^git@github\.com:([^/]+)/([^/]+?)(?:\.git)?/?$",
    re.IGNORECASE,
)
_GITHUB_SLUG = re.compile(r"^([\w.-]+)/([\w.-]+)$")

RunGh = Callable[..., tuple[bool, str, str]]


def _strip_dot_git(name: str) -> str:
    return name[: -len(".git")] if name.endswith(".git") else name


def parse_github_repo(git_url: str) -> Optional[tuple[str, str]]:
    """Extract ``(owner, repo)`` from common GitHub URL forms.

    Accepts HTTPS, ``git@github.com:owner/repo``, ``github.com/owner/repo``,
    and a bare ``owner/repo`` slug. Extra path segments (``/pull/7``) are
    ignored so a PR URL still names the repository.
    """
    url = str(git_url).strip()
    if not url:
        return None

    ssh = _GITHUB_SSH.match(url)
    if ssh:
        return ssh.group(1), _strip_dot_git(ssh.group(2))

    if "://" not in url and not url.startswith("git@"):
        slug = _GITHUB_SLUG.match(url)
        if slug is not None and slug.group(1).lower() not in (
            "github.com",
            "www.github.com",
        ):
            return slug.group(1), _strip_dot_git(slug.group(2))

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    parsed = urlparse(url)
    if parsed.hostname not in ("github.com", "www.github.com"):
        return None

    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) < 2:
        return None

    owner, repo = parts[0], _strip_dot_git(parts[1])
    if not owner or not repo:
        return None
    return owner, repo


def normalize_repo_url(git_url: str, owner: str, repo: str) -> str:
    """Canonical https URL for a parsed GitHub repo.

    ``git_url`` is unused; callers already have ``owner`` / ``repo`` from
    ``parse_github_repo``. Kept so existing call sites stay one-line swaps.
    """
    _ = git_url
    return f"https://github.com/{owner}/{repo}"


def canonical_github_repo(repo_url: str) -> tuple[str, str]:
    """``(clone_url, name)``. ``name`` is the repo, never ``owner/name``.

    Cursor's validation errors print GitHub's nameWithOwner
    (``SamJBoyer/SMOKETESTREPO``). MegaDesk, Floor, and WORKORDER identify the
    same repo as ``SMOKETESTREPO`` — the last path segment.

    GitHub forms are rewritten to ``https://github.com/owner/repo``. Anything
    else git can clone — including a local path, which is what the integration
    suite clones from — is passed through.
    """
    text = str(repo_url).strip()
    parsed = parse_github_repo(text)
    if parsed is not None:
        owner, repo = parsed
        return f"https://github.com/{owner}/{repo}", repo
    return text, repo_name_from_url(text)


def resolve_github_remote(
    git_url: str,
) -> tuple[Optional[tuple[str, str, str]], Optional[str]]:
    """Turn a pasted URL into ``(owner, repo, https_url)`` or a status line.

    Both GitHub pollers share this: empty field, not a GitHub URL, or a remote
    they can ``gh repo view``.
    """
    text = str(git_url or "").strip()
    if not text:
        return None, EMPTY_GITHUB_URL
    parsed = parse_github_repo(text)
    if parsed is None:
        return None, UNSUPPORTED_GITHUB_URL
    owner, repo = parsed
    return (owner, repo, f"https://github.com/{owner}/{repo}"), None


def run_gh(*args: str) -> tuple[bool, str, str]:
    """Run ``gh`` and return ``(ok, stdout, stderr_or_message)``."""
    try:
        result = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            timeout=GH_TIMEOUT_SEC,
            check=False,
        )
    except FileNotFoundError:
        return False, "", "gh CLI not found — install and authenticate GitHub CLI"
    except subprocess.TimeoutExpired:
        return False, "", "gh command timed out"

    if result.returncode != 0:
        err = (result.stderr or result.stdout or "gh command failed").strip()
        return False, result.stdout, err
    return True, result.stdout, ""


def list_github_issues(
    owner: str,
    repo: str,
    label: str,
    *,
    gh: Optional[RunGh] = None,
    state: str = "open",
    limit: int = 100,
) -> tuple[bool, list[dict[str, Any]], Optional[str]]:
    """Verify the remote, then list issues with ``label``.

    Returns ``(ok, items, error)``. Each item has ``number``, ``title``,
    ``body``. Pass ``gh=run_gh`` from the calling module so ``FakeGh`` can
    patch that module's binding without also patching this one.
    """
    runner = gh if gh is not None else run_gh
    slug = f"{owner}/{repo}"

    ok, _, err = runner("repo", "view", slug, "--json", "nameWithOwner")
    if not ok:
        return False, [], err or "Connection failed"

    ok, stdout, err = runner(
        "issue",
        "list",
        "--repo",
        slug,
        "--label",
        label,
        "--state",
        state,
        "--limit",
        str(limit),
        "--json",
        ISSUE_JSON_FIELDS,
    )
    if not ok:
        return False, [], err or "Failed to list issues"

    try:
        payload = json.loads(stdout or "[]")
    except json.JSONDecodeError as exc:
        return False, [], f"Invalid gh JSON: {exc}"
    if not isinstance(payload, list):
        return False, [], "Invalid gh JSON: expected a list"

    items: list[dict[str, Any]] = []
    for item in payload:
        number = item.get("number")
        if number is None:
            continue
        items.append(
            {
                "number": int(number),
                "title": item.get("title") or f"Issue #{number}",
                "body": item.get("body") or "",
            }
        )
    return True, items, None
