"""What every human gate reads off GitHub, defined once.

A **human gate** is a node where a person decides that a step may proceed:
WorkDispatcher hands an agent-ready ticket to a factory, AutoIntegrate sends an
agent at a pull request that stopped merging. What pressing the button means is
different enough that the two are separate nodes with separate wiring — see
``Nodes/HumanGates/README.md``. What they read is the same: which labels the
connected repo has, and which open issues carry the label this gate targets.

The merge-check markers are the other half of that reading, and they are a
contract with ``.github/workflows/merge-check.yml``. That workflow writes the
pull request number, its head branch and its base into the issue body; PRManager
and AutoIntegrate read them back. Both sides spell them from here, because an
issue that says which PR it is about is only useful while the two spellings
agree.

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

GH_TIMEOUT_SEC = 15
ISSUE_LIST_LIMIT = 100

LABEL_AGENT_READY = "agent-ready"
LABEL_MERGE_SUCCESS = "MERGE_SUCCESS"
LABEL_MERGE_FAIL = "MERGE_FAIL"

# Written as HTML comments so they are invisible on the issue page and exact for
# whoever parses them. The first one is also what merge-check greps for when it
# looks for the issue it already filed about a PR.
PR_MARKER = "megadesk:merge-check:pr-{number}"
BRANCH_MARKER = "megadesk:pr-branch:{branch}"
BASE_MARKER = "megadesk:pr-base:{base}"

_PR_MARKER_RE = re.compile(
    r"megadesk:(?:merge-check|merge_success|failed-merge):pr-(\d+)"
)
_BRANCH_MARKER_RE = re.compile(r"megadesk:pr-branch:([^\s>]+)")
_BASE_MARKER_RE = re.compile(r"megadesk:pr-base:([^\s>]+)")
_PR_URL_RE = re.compile(
    r"https://github\.com/[^/\s]+/[^/\s]+/pull/(\d+)", re.IGNORECASE
)

GhRunner = Callable[..., tuple[bool, str, str]]

__all__ = [
    "BASE_MARKER",
    "BRANCH_MARKER",
    "GH_TIMEOUT_SEC",
    "GateIssue",
    "ISSUE_LIST_LIMIT",
    "LABEL_AGENT_READY",
    "LABEL_MERGE_FAIL",
    "LABEL_MERGE_SUCCESS",
    "PR_MARKER",
    "PullRequestRef",
    "check_repo",
    "list_labeled_issues",
    "list_repo_labels",
    "merge_issue_markers",
    "normalize_repo_url",
    "parse_github_repo",
    "parse_pull_request_ref",
    "resolve_pull_request_ref",
    "run_gh",
]


@dataclass
class GateIssue:
    """One open issue carrying the label a gate targets."""

    number: int
    title: str
    body: str = ""


@dataclass
class PullRequestRef:
    """The pull request an issue is about, as far as the issue says.

    ``branch`` is the PR's head — the branch an agent has to stand on to fix it.
    ``base`` is what it failed to merge into. Either can be empty on an issue
    filed before merge-check wrote the markers; ``resolve_pull_request_ref``
    fills those in from GitHub.
    """

    number: int = 0
    url: str = ""
    branch: str = ""
    base: str = ""

    def __bool__(self) -> bool:
        return bool(self.number or self.url)


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


def merge_issue_markers(*, pr_number: Any, branch: str, base: str) -> str:
    """The marker block merge-check writes at the top of an issue body."""
    return "\n".join(
        (
            f"<!-- {PR_MARKER.format(number=pr_number)} -->",
            f"<!-- {BRANCH_MARKER.format(branch=branch)} -->",
            f"<!-- {BASE_MARKER.format(base=base)} -->",
        )
    )


def parse_pull_request_ref(
    body: str, owner: str = "", repo: str = ""
) -> PullRequestRef:
    """Read the PR an issue is about out of its body.

    The number comes from the marker or, failing that, from the PR link
    merge-check also writes; the branches only ever come from markers.
    """
    text = body or ""
    number = 0
    marker = _PR_MARKER_RE.search(text)
    if marker:
        number = int(marker.group(1))

    url = ""
    link = _PR_URL_RE.search(text)
    if link:
        url = link.group(0)
        number = number or int(link.group(1))
    elif number and owner and repo:
        url = f"https://github.com/{owner}/{repo}/pull/{number}"

    branch = _BRANCH_MARKER_RE.search(text)
    base = _BASE_MARKER_RE.search(text)
    return PullRequestRef(
        number=number,
        url=url,
        branch=branch.group(1) if branch else "",
        base=base.group(1) if base else "",
    )


def resolve_pull_request_ref(
    ref: PullRequestRef,
    owner: str,
    repo: str,
    *,
    gh: GhRunner = run_gh,
) -> PullRequestRef:
    """Fill in a missing head or base branch by asking GitHub about the PR.

    Issues filed before merge-check wrote the branch markers carry only a
    number, and a gate that refused those would be blind to every PR opened
    before this workflow shipped.
    """
    if not ref.number or (ref.branch and ref.base):
        return ref
    ok, stdout, _err = gh(
        "pr",
        "view",
        str(ref.number),
        "--repo",
        f"{owner}/{repo}",
        "--json",
        "url,headRefName,baseRefName",
    )
    if not ok:
        return ref
    try:
        payload = json.loads(stdout or "{}")
    except json.JSONDecodeError:
        return ref
    return PullRequestRef(
        number=ref.number,
        url=ref.url or str(payload.get("url") or ""),
        branch=ref.branch or str(payload.get("headRefName") or ""),
        base=ref.base or str(payload.get("baseRefName") or ""),
    )
