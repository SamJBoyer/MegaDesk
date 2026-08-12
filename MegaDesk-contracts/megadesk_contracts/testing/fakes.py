"""Stand-ins for the parts of the chain that are not the seam under test.

``FakeGh`` removes GitHub (network, auth, rate limits) from TicketDispatcher's
poll loop. ``FakeAgent`` replaces the sandbox boundary — Floor cloning, Docker
and ``cursor_sdk`` — while keeping everything on both sides of it real: the
WORKORDER consumer group, the wire payloads, the git worktrees, and the
FINISHED stream MergeManager reads.
"""

from __future__ import annotations

import contextlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Optional


@dataclass
class Issue:
    number: int
    title: str
    body: str = ""


class FakeGh:
    """Canned replacement for ``ticket_dispatcher_app.run_gh``.

    Answers the two real invocations — ``gh repo view`` and
    ``gh issue list --label agent-ready … --json number,title,body`` — and fails
    loudly on anything else so a changed argv surfaces as a test failure rather
    than a hang.
    """

    def __init__(
        self,
        *,
        issues: Optional[list[Issue]] = None,
        repo_error: str = "",
        issue_error: str = "",
    ) -> None:
        self.issues: list[Issue] = list(issues or [])
        self.repo_error = repo_error
        self.issue_error = issue_error
        self.calls: list[tuple[str, ...]] = []

    def add_issue(self, number: int, title: str, body: str = "") -> Issue:
        issue = Issue(number=number, title=title, body=body)
        self.issues.append(issue)
        return issue

    @property
    def repo_views(self) -> int:
        return sum(1 for call in self.calls if call[:2] == ("repo", "view"))

    @property
    def issue_lists(self) -> int:
        return sum(1 for call in self.calls if call[:2] == ("issue", "list"))

    def __call__(self, *args: str) -> tuple[bool, str, str]:
        self.calls.append(tuple(args))
        if args[:2] == ("repo", "view"):
            if self.repo_error:
                return False, "", self.repo_error
            slug = args[2] if len(args) > 2 else ""
            return True, json.dumps({"nameWithOwner": slug}), ""
        if args[:2] == ("issue", "list"):
            if self.issue_error:
                return False, "", self.issue_error
            payload = [
                {"number": i.number, "title": i.title, "body": i.body}
                for i in self.issues
            ]
            return True, json.dumps(payload), ""
        return False, "", f"FakeGh received an unexpected invocation: gh {' '.join(args)}"

    @contextlib.contextmanager
    def install(self, module: Any, attribute: str = "run_gh") -> Iterator["FakeGh"]:
        """Swap ``module.run_gh`` for this fake for the duration of the block."""
        original = getattr(module, attribute)
        setattr(module, attribute, self)
        try:
            yield self
        finally:
            setattr(module, attribute, original)


@dataclass
class AgentRun:
    """One WORKORDER the fake agent consumed."""

    workorder_id: str
    repo: str
    ticket_name: str
    model: str
    new_wt: bool
    wt: Path
    agent_dir: Path
    commit_sha: str
    finished_id: str
    finished_stream: str


def _safe_name(name: str) -> str:
    cleaned = name.strip().replace(" ", "-")
    if not cleaned or not re.match(r"^[\w.-]+$", cleaned):
        raise ValueError(f"Invalid name for a worktree: {name!r}")
    return cleaned


class FakeAgent:
    """Consumes WORKORDER, commits in a real worktree, publishes FINISHED.

    Reads through the real ``mission_control`` consumer group with the real
    ack semantics, and builds its payloads with the production wire helpers
    injected as ``wire`` — so a renamed field or a dropped ack fails here.
    """

    def __init__(
        self,
        *,
        redis: Any,
        floor: Any,
        wire: Any,
        group: str = "mission_control",
        consumer: str = "fake-agent",
        commit_relpath: str = "agent.txt",
        commit_text: str = "work done by the fake agent\n",
        ticket_worktree: Optional[Callable[[str, str], Path]] = None,
    ) -> None:
        self.redis = redis
        self.floor = floor
        self.wire = wire
        self.group = group
        self.consumer = consumer
        self.commit_relpath = commit_relpath
        self.commit_text = commit_text
        self._ticket_worktree = ticket_worktree
        self.runs: list[AgentRun] = []

    # --- consumer group ---

    def ensure_group(self) -> None:
        from redis.exceptions import ResponseError

        try:
            self.redis.xgroup_create(
                self.wire.WORKORDER_STREAM, self.group, id="0", mkstream=True
            )
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def pending(self) -> int:
        """Entries delivered to the group but not yet acked."""
        info = self.redis.xpending(self.wire.WORKORDER_STREAM, self.group)
        if isinstance(info, dict):
            return int(info.get("pending") or 0)
        return int(info[0]) if info else 0

    # --- work ---

    def run_once(self, *, count: int = 32) -> list[AgentRun]:
        """Drain pending then new WORKORDER entries, one pass."""
        self.ensure_group()
        produced: list[AgentRun] = []
        for stream_id in ("0", ">"):
            results = self.redis.xreadgroup(
                groupname=self.group,
                consumername=self.consumer,
                streams={self.wire.WORKORDER_STREAM: stream_id},
                count=count,
            )
            for _stream, messages in results or []:
                for entry_id, fields in messages:
                    produced.append(self.handle(entry_id, fields))
        self.runs.extend(produced)
        return produced

    def handle(self, entry_id: str, fields: dict[str, Any]) -> AgentRun:
        item = self.wire.parse_workorder(fields)
        repo = item["repo"]
        ticket_name = item["ticket_name"]

        if item["new_wt"]:
            wt = self._make_ticket_worktree(repo, _safe_name(ticket_name))
        else:
            wt = Path(item["wt"])
            if not wt.is_absolute():
                raise AssertionError(f"WORKORDER wt must be absolute: {wt}")
            if not wt.is_dir():
                raise AssertionError(f"WORKORDER wt does not exist: {wt}")

        sha = self.floor.commit(
            wt,
            self.commit_relpath,
            self.commit_text,
            f"agent: {ticket_name}",
        )

        agent_dir = Path(self.floor.agents_dir).resolve()
        payload = self.wire.finished_fields(
            ticket_name=ticket_name,
            ticket_id=str(entry_id),
            wt=str(wt.resolve()),
            agent_dir=str(agent_dir),
        )
        stream = self.wire.finished_stream(repo)
        finished_id = self.redis.xadd(stream, payload)
        self.redis.xack(self.wire.WORKORDER_STREAM, self.group, entry_id)

        return AgentRun(
            workorder_id=str(entry_id),
            repo=repo,
            ticket_name=ticket_name,
            model=item["model"],
            new_wt=bool(item["new_wt"]),
            wt=wt.resolve(),
            agent_dir=agent_dir,
            commit_sha=sha,
            finished_id=str(finished_id),
            finished_stream=stream,
        )

    def _make_ticket_worktree(self, repo: str, ticket: str) -> Path:
        if self._ticket_worktree is not None:
            return Path(self._ticket_worktree(repo, ticket))
        existing = self.floor.ticket_dir(ticket)
        if existing.is_dir():
            return existing
        return Path(self.floor.add_ticket(ticket))
