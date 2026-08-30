"""What every human gate reads off GitHub, defined once.

A **human gate** is a node where a person decides that a step may proceed:
WorkDispatcher hands an agent-ready ticket to a factory, AutoIntegrate sends an
agent at a pull request that stopped merging. What pressing the button means is
different enough that the two are separate nodes with separate wiring — see
``Nodes/HumanGates/README.md``. WorkDispatcher still reads issue labels.
AutoIntegrate and PRManager read the ``mergeable`` check
``.github/workflows/merge-check.yml`` posts on each PR head: failure is
AutoIntegrate's queue, success is PRManager's.

``gh`` is injected rather than called directly by the list helpers, so a caller
that has swapped its own module-level ``run_gh`` (the integration suite does)
still gets its stand-in used here.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from megadesk_contracts.wire.factory import DEFAULT_STARTING_REF

GH_TIMEOUT_SEC = 15
ISSUE_LIST_LIMIT = 100

LABEL_AGENT_READY = "agent-ready"
LABEL_IN_PROGRESS = "in-progress"

# Check name merge-check posts, and the only name AutoIntegrate / PRManager
# look for on ``statusCheckRollup``. Spelled again in
# ``.github/workflows/merge-check.yml``.
MERGE_CHECK_CONTEXT = "mergeable"
MERGE_CHECK_SUCCESS = "success"
MERGE_CHECK_FAILURE = "failure"

# GitHub issue bodies carry screenshots as markdown images or <img src>.
_MD_IMAGE = re.compile(r"!\[(?:[^\]]*)\]\((https?://[^)\s]+)\)")
_HTML_IMAGE = re.compile(
    r"<img\b[^>]*\bsrc=['\"](https?://[^'\"]+)['\"]",
    re.IGNORECASE,
)

_SUCCESS_STATES = frozenset({"SUCCESS"})
_FAILURE_STATES = frozenset({"FAILURE", "ERROR", "TIMED_OUT"})

GhRunner = Callable[..., tuple[bool, str, str]]

__all__ = [
    "DEFAULT_STARTING_REF",
    "GH_TIMEOUT_SEC",
    "GateIssue",
    "GatePullRequest",
    "ISSUE_LIST_LIMIT",
    "LABEL_AGENT_READY",
    "LABEL_IN_PROGRESS",
    "MERGE_CHECK_CONTEXT",
    "MERGE_CHECK_FAILURE",
    "MERGE_CHECK_SUCCESS",
    "check_repo",
    "extract_issue_pictures",
    "list_labeled_issues",
    "list_merge_prs",
    "list_repo_labels",
    "merge_check_verdict",
    "normalize_repo_url",
    "parse_github_repo",
    "relabel_issue",
    "run_gh",
]


@dataclass
class GateIssue:
    """One open issue carrying the label a gate targets."""

    number: int
    title: str
    body: str = ""


@dataclass
class GatePullRequest:
    """One open PR merge-check has posted a ``mergeable`` status on."""

    number: int
    title: str
    url: str
    branch: str
    base: str


def run_gh(*args: str) -> tuple[bool, str, str]:
    """Run the GitHub CLI. ``(ok, stdout, error)``; never raises."""
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


def parse_github_repo(git_url: str) -> Optional[tuple[str, str]]:
    """Extract (owner, repo) from common GitHub URL forms."""
    url = (git_url or "").strip()
    if not url:
        return None

    ssh = re.match(r"git@github\.com:([^/]+)/([^/]+?)(?:\.git)?/?$", url)
    if ssh:
        return ssh.group(1), ssh.group(2)

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    parsed = urlparse(url)
    if parsed.hostname not in ("github.com", "www.github.com"):
        return None

    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) < 2:
        return None

    owner, repo = parts[0], parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    return owner, repo


def normalize_repo_url(git_url: str, owner: str, repo: str) -> str:
    return f"https://github.com/{owner}/{repo}"


def extract_issue_pictures(body: str) -> list[str]:
    """Image URLs embedded in a GitHub issue body, in document order.

    WorkDispatcher puts these on the order so a factory can attach them as
    agent context. Markdown ``![alt](url)`` and HTML ``<img src>`` are the
    two forms GitHub actually writes when someone drops a screenshot on an
    issue; a bare URL in the text is not a picture.
    """
    text = body or ""
    found: list[tuple[int, str]] = []
    for match in _MD_IMAGE.finditer(text):
        found.append((match.start(), match.group(1)))
    for match in _HTML_IMAGE.finditer(text):
        found.append((match.start(), match.group(1)))
    seen: set[str] = set()
    out: list[str] = []
    for _pos, url in sorted(found, key=lambda item: item[0]):
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out


def check_repo(owner: str, repo: str, *, gh: GhRunner = run_gh) -> tuple[bool, str]:
    """Whether the CLI can see this repo at all. ``(ok, error)``."""
    ok, _stdout, err = gh("repo", "view", f"{owner}/{repo}", "--json", "nameWithOwner")
    return (True, "") if ok else (False, err or "Connection failed")


def list_repo_labels(
    owner: str, repo: str, *, gh: GhRunner = run_gh, limit: int = ISSUE_LIST_LIMIT
) -> tuple[bool, list[str], str]:
    """Every issue label defined on the repo, for a gate's target dropdown."""
    ok, stdout, err = gh(
        "label",
        "list",
        "--repo",
        f"{owner}/{repo}",
        "--limit",
        str(limit),
        "--json",
        "name",
    )
    if not ok:
        return False, [], err or "Failed to list labels"
    try:
        payload = json.loads(stdout or "[]")
    except json.JSONDecodeError as exc:
        return False, [], f"Invalid gh JSON: {exc}"
    names = [str(item.get("name") or "") for item in payload]
    return True, [name for name in names if name], ""


def list_labeled_issues(
    owner: str,
    repo: str,
    label: str,
    *,
    gh: GhRunner = run_gh,
    limit: int = ISSUE_LIST_LIMIT,
) -> tuple[bool, list[GateIssue], str]:
    """Open issues carrying ``label``, newest listing first."""
    ok, stdout, err = gh(
        "issue",
        "list",
        "--repo",
        f"{owner}/{repo}",
        "--label",
        label,
        "--state",
        "open",
        "--limit",
        str(limit),
        "--json",
        "number,title,body",
    )
    if not ok:
        return False, [], err or "Failed to list issues"
    try:
        payload = json.loads(stdout or "[]")
    except json.JSONDecodeError as exc:
        return False, [], f"Invalid gh JSON: {exc}"

    issues: list[GateIssue] = []
    for item in payload:
        number = item.get("number")
        if number is None:
            continue
        issues.append(
            GateIssue(
                number=int(number),
                title=item.get("title") or f"Issue #{number}",
                body=item.get("body") or "",
            )
        )
    return True, issues, ""


def relabel_issue(
    owner: str,
    repo: str,
    number: int,
    *,
    add: str = LABEL_IN_PROGRESS,
    remove: str = LABEL_AGENT_READY,
    gh: GhRunner = run_gh,
) -> tuple[bool, str]:
    """Move an issue from one label onto another. ``(ok, error)``.

    WorkDispatcher does this when an operator clicks a ticket: ``agent-ready``
    comes off and ``in-progress`` goes on, so the gate's next poll does not
    offer the same issue again. ``add`` is created on the repo if missing;
    a create that fails because the label already exists is ignored.
    """
    slug = f"{owner}/{repo}"
    if add:
        gh("label", "create", add, "--repo", slug, "--color", "D93F0B")
    args = ["issue", "edit", str(int(number)), "--repo", slug]
    if add:
        args.extend(["--add-label", add])
    if remove and remove != add:
        args.extend(["--remove-label", remove])
    ok, _stdout, err = gh(*args)
    return (True, "") if ok else (False, err or "Failed to update issue labels")


def merge_check_verdict(rollup: Any) -> Optional[str]:
    """Latest ``mergeable`` signal on a PR: ``success``, ``failure``, or None.

    GitHub's Actions job on a PR does not re-run when ``dev`` moves, so
    merge-check posts a check named ``mergeable`` onto the PR head. A later
    check on the same SHA is a new run; the newest timestamp wins. The
    rollup can still contain a stale job named ``report``; only
    ``MERGE_CHECK_CONTEXT`` counts.
    """
    chosen: Optional[tuple[str, str]] = None
    for item in rollup or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("context") or "")
        if name != MERGE_CHECK_CONTEXT:
            continue
        raw = str(item.get("conclusion") or item.get("state") or "").upper()
        if raw in _SUCCESS_STATES:
            verdict = MERGE_CHECK_SUCCESS
        elif raw in _FAILURE_STATES:
            verdict = MERGE_CHECK_FAILURE
        else:
            continue
        when = str(item.get("completedAt") or item.get("startedAt") or "")
        if chosen is None or when >= chosen[0]:
            chosen = (when, verdict)
    return chosen[1] if chosen else None


def list_merge_prs(
    owner: str,
    repo: str,
    verdict: str,
    *,
    gh: GhRunner = run_gh,
    base: str = DEFAULT_STARTING_REF,
    limit: int = ISSUE_LIST_LIMIT,
) -> tuple[bool, list[GatePullRequest], str]:
    """Open PRs into ``base`` whose latest ``mergeable`` status is ``verdict``."""
    wanted = (verdict or "").strip().lower()
    if wanted not in (MERGE_CHECK_SUCCESS, MERGE_CHECK_FAILURE):
        return False, [], f"Unknown merge-check verdict {verdict!r}"

    ok, stdout, err = gh(
        "pr",
        "list",
        "--repo",
        f"{owner}/{repo}",
        "--base",
        base,
        "--state",
        "open",
        "--limit",
        str(limit),
        "--json",
        "number,title,url,headRefName,baseRefName,statusCheckRollup",
    )
    if not ok:
        return False, [], err or "Failed to list pull requests"
    try:
        payload = json.loads(stdout or "[]")
    except json.JSONDecodeError as exc:
        return False, [], f"Invalid gh JSON: {exc}"

    prs: list[GatePullRequest] = []
    for item in payload:
        number = item.get("number")
        if number is None:
            continue
        if merge_check_verdict(item.get("statusCheckRollup")) != wanted:
            continue
        prs.append(
            GatePullRequest(
                number=int(number),
                title=item.get("title") or f"PR #{number}",
                url=str(item.get("url") or "")
                or f"https://github.com/{owner}/{repo}/pull/{int(number)}",
                branch=str(item.get("headRefName") or ""),
                base=str(item.get("baseRefName") or base),
            )
        )
    return True, prs, ""
