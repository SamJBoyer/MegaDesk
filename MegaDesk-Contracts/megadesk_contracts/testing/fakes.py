"""Stand-ins for the parts of the chain that are not the seam under test.

``FakeGh`` removes GitHub (network, auth, rate limits) from TicketDispatcher
and PRManager poll loops. ``FakeAgent`` replaces the sandbox boundary — clone,
Docker, Redis sidecar and ``cursor_sdk`` — while keeping everything on both
sides of it real: the WORKORDER consumer group, the wire payloads, and the
FINISHED stream the factory publishes. It publishes a canned PR URL rather
than depending on Floor.

The three newer fakes cut the same way for the voice chain, each at the lowest
boundary that still removes a network or a device:

* ``FakeCodeAgent`` — CODEQ:ASK in, canned CODEQ:ANSWER chunks out. No
  ``cursor_sdk``, and usable either as a whole stand-in BE or as the chunk source
  injected into the real one.
* ``FakeRealtime`` — a scripted OpenAI Realtime socket. No audio device, no
  websocket, so the real tool router runs against real event shapes.
* ``FakeCloudFactory`` — ``bc-`` agent ids and a canned PR URL instead of a
  Cursor-hosted VM, including both failure modes a factory must separate.
* ``FakeMachineFactory`` — sandbox guids and a container that stops when told,
  instead of a Docker daemon.

The last two implement ``megadesk_contracts.factory.AgentFactory``, so a test can
hand either to a graph and assert the same three verbs behave the same way.
"""

from __future__ import annotations

import contextlib
import json
import re
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Optional, Sequence

from megadesk_contracts.agent_errors import AgentRunError, AgentStartupError
from megadesk_contracts.factory import RunHandle, RunStatus
from megadesk_contracts.realtime import (
    EVENT_ASSISTANT_TEXT,
    EVENT_ERROR,
    EVENT_STATE,
    EVENT_TOOL_CALL,
    EVENT_TRANSCRIPT_FINAL,
    EVENT_TRANSCRIPT_PARTIAL,
    RealtimeEvent,
)
from megadesk_contracts.wire import cloud as cloud_wire
from megadesk_contracts.wire import code_scope as code_scope_wire
from megadesk_contracts.wire import factory as factory_wire


LABEL_AGENT_READY = "agent-ready"
LABEL_MERGE_SUCCESS = "MERGE_SUCCESS"


@dataclass
class Issue:
    number: int
    title: str
    body: str = ""
    labels: tuple[str, ...] = (LABEL_AGENT_READY,)
    state: str = "open"


def _flag(args: tuple[str, ...], name: str) -> str:
    try:
        index = args.index(name)
    except ValueError:
        return ""
    if index + 1 >= len(args):
        return ""
    return args[index + 1]


class FakeGh:
    """Canned replacement for ``run_gh`` on TicketDispatcher and PRManager.

    Answers ``gh repo view``, ``gh issue list --label …``, and ``gh issue close``,
    and fails loudly on anything else so a changed argv surfaces as a test
    failure rather than a hang. ``issue list`` filters by ``--label`` and
    ``--state`` the way the real CLI does. Nodes re-export
    ``megadesk_contracts.github.run_gh``; patch that binding on the node module.
    """

    def __init__(
        self,
        *,
        issues: Optional[list[Issue]] = None,
        repo_error: str = "",
        issue_error: str = "",
        close_error: str = "",
    ) -> None:
        self.issues: list[Issue] = list(issues or [])
        self.repo_error = repo_error
        self.issue_error = issue_error
        self.close_error = close_error
        self.calls: list[tuple[str, ...]] = []

    def add_issue(
        self,
        number: int,
        title: str,
        body: str = "",
        *,
        labels: Optional[Sequence[str]] = None,
        state: str = "open",
    ) -> Issue:
        issue = Issue(
            number=number,
            title=title,
            body=body,
            labels=tuple(labels) if labels is not None else (LABEL_AGENT_READY,),
            state=state,
        )
        self.issues.append(issue)
        return issue

    def add_merge_success(
        self,
        number: int,
        title: str,
        pr_url: str,
        *,
        pr_number: int | None = None,
    ) -> Issue:
        """File the issue merge-check would open after a clean PR merge-tree."""
        pr = pr_number
        if pr is None:
            match = re.search(r"/pull/(\d+)", pr_url)
            pr = int(match.group(1)) if match else number
        body = (
            f"<!-- megadesk:merge-check:pr-{pr} -->\n\n"
            f"{pr_url} merges into `dev`.\n"
        )
        return self.add_issue(
            number, title, body, labels=(LABEL_MERGE_SUCCESS,)
        )

    @property
    def repo_views(self) -> int:
        return sum(1 for call in self.calls if call[:2] == ("repo", "view"))

    @property
    def issue_lists(self) -> int:
        return sum(1 for call in self.calls if call[:2] == ("issue", "list"))

    @property
    def issue_closes(self) -> int:
        return sum(1 for call in self.calls if call[:2] == ("issue", "close"))

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
            label = _flag(args, "--label")
            state = _flag(args, "--state") or "open"
            payload = [
                {"number": i.number, "title": i.title, "body": i.body}
                for i in self.issues
                if (not label or label in i.labels) and i.state == state
            ]
            return True, json.dumps(payload), ""
        if args[:2] == ("issue", "close"):
            if self.close_error:
                return False, "", self.close_error
            try:
                number = int(args[2])
            except (IndexError, ValueError):
                return False, "", "FakeGh issue close needs a number"
            for issue in self.issues:
                if issue.number == number:
                    issue.state = "closed"
                    return True, "", ""
            return False, "", f"Issue #{number} not found"
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
    commit_sha: str
    finished_id: str
    finished_stream: str
    pr_url: str = ""
    status: str = ""


class FakeAgent:
    """Consumes WORKORDER and publishes FINISHED with a canned PR URL.

    Reads through MachineFactory's real consumer group with the real ack
    semantics, and builds its payloads with the production wire helpers injected
    as ``wire`` — so a renamed field or a dropped ack fails here. No Floor,
    worktree, or Docker: callers only need ``status`` and ``pr_url``.
    """

    def __init__(
        self,
        *,
        redis: Any,
        wire: Any,
        floor: Any = None,
        group: Optional[str] = None,
        consumer: str = "fake-agent",
        commit_relpath: str = "agent.txt",
        commit_text: str = "work done by the fake agent\n",
        ticket_worktree: Optional[Callable[[str, str], Path]] = None,
        pr_url_template: str = "https://github.com/acme/{repo}/pull/{n}",
    ) -> None:
        self.redis = redis
        self.floor = floor
        self.wire = wire
        self.group = group or wire.WORKORDER_GROUP
        self.consumer = consumer
        self.commit_relpath = commit_relpath
        self.commit_text = commit_text
        self._ticket_worktree = ticket_worktree
        self.pr_url_template = pr_url_template
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

        commit_sha = ""
        if self.floor is not None and self._ticket_worktree is not None:
            # Optional path for tests that still want a real commit.
            wt = Path(self._ticket_worktree(repo, ticket_name))
            commit_sha = self.floor.commit(
                wt,
                self.commit_relpath,
                self.commit_text,
                f"agent: {ticket_name}",
            )

        pr_url = self.pr_url_template.format(repo=repo, n=len(self.runs) + 1)
        status = self.wire.STATUS_FINISHED
        payload = self.wire.finished_fields(
            ticket_name=ticket_name,
            ticket_id=str(entry_id),
            status=status,
            pr_url=pr_url,
        )
        stream = self.wire.finished_stream(repo)
        finished_id = self.redis.xadd(stream, payload)
        self.redis.xack(self.wire.WORKORDER_STREAM, self.group, entry_id)

        return AgentRun(
            workorder_id=str(entry_id),
            repo=repo,
            ticket_name=ticket_name,
            model=item["model"],
            commit_sha=commit_sha,
            finished_id=str(finished_id),
            finished_stream=stream,
            pr_url=pr_url,
            status=status,
        )


# --- CodeScope -------------------------------------------------------------

DEFAULT_CANNED_ANSWER = (
    "The shared frame pump is a module global, so it outlives the Dear PyGui "
    "context. Whoever owns the context calls reset on teardown."
)


def split_sentences(text: str) -> list[str]:
    """Split answer text the way a streaming reader would consume it.

    The real runner yields whatever the agent emits, which is neither one chunk
    nor one word. Sentences are the unit VoiceDeck speaks, so faking at that
    granularity exercises the multi-entry answer path rather than pretending a
    whole answer arrives at once.

    Separators stay attached to the chunk before them, so concatenating the
    chunks reproduces the input exactly — as a real token stream does. A fake
    that dropped them would hide a consumer that forgets to space its joins.
    """
    raw = str(text)
    pieces = re.split(r"(?<=[.!?])(\s+)", raw)
    chunks: list[str] = []
    for index in range(0, len(pieces), 2):
        separator = pieces[index + 1] if index + 1 < len(pieces) else ""
        chunk = pieces[index] + separator
        if chunk.strip():
            chunks.append(chunk)
    return chunks or [raw]


@dataclass
class CodeAnswer:
    """One CODEQ:ASK the fake consumed, and everything it published for it."""

    ask_id: str
    session_id: str
    question_id: str
    repo: str
    question: str
    mode: str
    chunks: list[str]
    answer: str
    status: str
    answer_ids: list[str]


class FakeRunner:
    """What ``CodeScopeManager`` sees instead of a local Cursor agent."""

    def __init__(
        self,
        owner: "FakeCodeAgent",
        *,
        cwd: Any = None,
        model: str = "auto",
        agent_id: str = "",
        **_ignored: Any,
    ) -> None:
        self.owner = owner
        self.cwd = cwd
        self.model = model
        self.agent_id = agent_id or owner.agent_id
        self.closed = False

    def answer(self, question: str, *, mode: str = code_scope_wire.MODE_ANSWER):
        self.owner.questions.append(question)
        self.owner.modes.append(mode)
        if self.owner.startup_error:
            raise AgentStartupError(self.owner.startup_error)
        if self.owner.run_error:
            raise AgentRunError(self.owner.run_error)
        # A generator would defer the raises above until first iteration, which
        # is not where the manager expects to catch them.
        return iter(self.owner.chunks(question))

    def close(self) -> None:
        self.closed = True


class FakeCodeAgent:
    """Answers questions about code without ``cursor_sdk`` or a real clone.

    Two faces, because there are two seams worth cutting independently:

    * ``run_once()`` reads CODEQ:ASK through the real consumer group and
      publishes CODEQ:ANSWER with the production wire helpers, so an FE test
      needs no BE process at all.
    * ``runner_factory`` hands the real ``CodeScopeManager`` something that
      streams canned chunks, so the manager's own loop — sentence buffering,
      session status, ``agent_id`` persistence, error answers — runs for real.
    """

    def __init__(
        self,
        *,
        redis: Any,
        wire: Any = code_scope_wire,
        group: str = code_scope_wire.ASK_GROUP,
        consumer: str = "fake-code-agent",
        default_answer: str = DEFAULT_CANNED_ANSWER,
        agent_id: str = "fake-agent-001",
    ) -> None:
        self.redis = redis
        self.wire = wire
        self.group = group
        self.consumer = consumer
        self.default_answer = default_answer
        self.agent_id = agent_id
        self.answers: dict[str, str] = {}
        self.error = ""
        self.startup_error = ""
        self.run_error = ""
        self.runs: list[CodeAnswer] = []
        self.questions: list[str] = []
        self.modes: list[str] = []
        self.runners: list[FakeRunner] = []

    # --- runner face ---

    def runner_factory(self, **kwargs: Any) -> FakeRunner:
        runner = FakeRunner(self, **kwargs)
        self.runners.append(runner)
        return runner

    # --- canned answers ---

    def add_answer(self, contains: str, answer: str) -> None:
        """Answer questions containing ``contains`` (case-insensitive) with ``answer``."""
        self.answers[contains.strip().lower()] = answer

    def answer_for(self, question: str) -> str:
        text = str(question).strip().lower()
        for needle, answer in self.answers.items():
            if needle in text:
                return answer
        return self.default_answer

    def chunks(self, question: str) -> list[str]:
        return split_sentences(self.answer_for(question))

    # --- consumer group ---

    def ensure_group(self) -> None:
        from redis.exceptions import ResponseError

        try:
            self.redis.xgroup_create(
                self.wire.ASK_STREAM, self.group, id="0", mkstream=True
            )
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def pending(self) -> int:
        info = self.redis.xpending(self.wire.ASK_STREAM, self.group)
        if isinstance(info, dict):
            return int(info.get("pending") or 0)
        return int(info[0]) if info else 0

    # --- work ---

    def run_once(self, *, count: int = 32) -> list[CodeAnswer]:
        """Drain pending then new CODEQ:ASK entries, one pass."""
        self.ensure_group()
        produced: list[CodeAnswer] = []
        for stream_id in ("0", ">"):
            results = self.redis.xreadgroup(
                groupname=self.group,
                consumername=self.consumer,
                streams={self.wire.ASK_STREAM: stream_id},
                count=count,
            )
            for _stream, messages in results or []:
                for entry_id, fields in messages:
                    produced.append(self.handle(entry_id, fields))
        self.runs.extend(produced)
        return produced

    def handle(self, entry_id: str, fields: dict[str, Any]) -> CodeAnswer:
        ask = self.wire.parse_ask(fields)
        self.questions.append(ask["question"])
        self.modes.append(ask["mode"])

        if self.error:
            chunks = [self.error]
            status = self.wire.STATUS_ERROR
        else:
            chunks = self.chunks(ask["question"])
            status = self.wire.STATUS_OK

        answer_ids: list[str] = []
        for index, chunk in enumerate(chunks):
            payload = self.wire.answer_fields(
                session_id=ask["session_id"],
                question_id=ask["question_id"],
                repo=ask["repo"],
                answer=chunk,
                final=index == len(chunks) - 1,
                status=status,
            )
            answer_ids.append(str(self.redis.xadd(self.wire.ANSWER_STREAM, payload)))

        self.redis.xack(self.wire.ASK_STREAM, self.group, entry_id)

        return CodeAnswer(
            ask_id=str(entry_id),
            session_id=ask["session_id"],
            question_id=ask["question_id"],
            repo=ask["repo"],
            question=ask["question"],
            mode=ask["mode"],
            chunks=chunks,
            answer="".join(chunks),
            status=status,
            answer_ids=answer_ids,
        )


# --- VoiceDeck -------------------------------------------------------------


class FakeRealtime:
    """Scripted stand-in for the OpenAI Realtime socket: no audio, no network.

    Everything the BE does *around* the socket stays real — the tool router, the
    Redis events, and the out-of-band answer injection that keeps a 30-second
    codebase question from stalling a sub-second voice loop. Injected text is
    echoed back as an ``assistant_text`` event by default, which is what the real
    model does when handed a conversation item plus ``response.create``.
    """

    def __init__(
        self,
        *,
        script: Optional[list[RealtimeEvent]] = None,
        echo_injected: bool = True,
    ) -> None:
        self.pending: deque[RealtimeEvent] = deque(script or [])
        self.echo_injected = echo_injected
        self.tool_results: list[tuple[str, dict[str, Any]]] = []
        self.injected: list[str] = []
        self.connected = False
        self.closed = False
        self.muted = False
        self.mute_calls: list[bool] = []
        self._call_seq = 0

    # --- scripting ---

    def push(self, event: RealtimeEvent) -> RealtimeEvent:
        self.pending.append(event)
        return event

    def say(self, text: str, *, partial: bool = False) -> RealtimeEvent:
        """Script a user turn."""
        kind = EVENT_TRANSCRIPT_PARTIAL if partial else EVENT_TRANSCRIPT_FINAL
        return self.push(RealtimeEvent(kind=kind, text=text))

    def call_tool(
        self, name: str, arguments: Optional[dict[str, Any]] = None, *, call_id: str = ""
    ) -> str:
        """Script a function call, returning the call id to match its result."""
        self._call_seq += 1
        cid = call_id or f"call_{self._call_seq}"
        self.push(
            RealtimeEvent(
                kind=EVENT_TOOL_CALL,
                name=name,
                arguments=dict(arguments or {}),
                call_id=cid,
            )
        )
        return cid

    # --- transport surface the BE uses ---

    def connect(self) -> None:
        self.connected = True

    def close(self) -> None:
        self.connected = False
        self.closed = True

    def events(self) -> Iterator[RealtimeEvent]:
        """Yield scripted events until the script runs dry, then stop.

        Non-blocking on purpose: production blocks on a socket, but a test that
        blocks forever is a hang rather than a failure. Events appended while
        iterating (a tool result that provokes another turn) are still delivered.
        """
        while self.pending:
            yield self.pending.popleft()

    def set_muted(self, muted: bool) -> None:
        self.muted = bool(muted)
        self.mute_calls.append(bool(muted))

    def send_tool_result(self, call_id: str, payload: dict[str, Any]) -> None:
        self.tool_results.append((str(call_id), dict(payload)))

    def inject_assistant_text(self, text: str) -> None:
        self.injected.append(text)
        if self.echo_injected:
            self.push(RealtimeEvent(kind=EVENT_ASSISTANT_TEXT, text=text))

    # --- inspection ---

    def result_for(self, call_id: str) -> Optional[dict[str, Any]]:
        for cid, payload in self.tool_results:
            if cid == str(call_id):
                return payload
        return None


# --- Factories -------------------------------------------------------------


class FakeCloudFactory:
    """``bc-`` ids and a canned PR URL instead of a Cursor-hosted VM.

    Models both failure modes a factory has to keep apart: ``startup_error``
    raises before an agent id exists, while ``run_error`` produces a real agent
    that finishes badly.
    """

    def __init__(
        self,
        *,
        pr_url_template: str = "https://github.com/acme/widgets/pull/{n}",
        startup_error: str = "",
        run_error: str = "",
        retryable: bool = False,
        polls_before_finish: int = 1,
    ) -> None:
        self.pr_url_template = pr_url_template
        self.startup_error = startup_error
        self.run_error = run_error
        self.retryable = retryable
        self.polls_before_finish = max(0, int(polls_before_finish))
        self.launches: list[dict[str, Any]] = []
        self.cancelled: list[str] = []
        self._polls: dict[str, int] = {}
        self._seq = 0

    def launch(self, order: Any) -> RunHandle:
        if self.startup_error:
            raise AgentStartupError(self.startup_error, retryable=self.retryable)
        self._seq += 1
        agent_id = f"{cloud_wire.CLOUD_AGENT_ID_PREFIX}fake{self._seq:03d}"
        self.launches.append(
            {
                "agent_id": agent_id,
                "repo_url": str(order["repo_url"]),
                "instructions": str(order["instructions"]),
                "title": str(order["title"]),
                "model": str(order.get("model") or ""),
                "auto_pr": bool(order.get("auto_pr", True)),
                "ref": str(order.get("ref") or ""),
            }
        )
        return RunHandle(run_key=agent_id, run_id=f"run_{self._seq:03d}")

    def poll(self, run_key: str) -> RunStatus:
        seen = self._polls.get(run_key, 0) + 1
        self._polls[run_key] = seen
        if run_key in self.cancelled:
            return RunStatus(status=factory_wire.STATUS_CANCELLED)
        if seen <= self.polls_before_finish:
            return RunStatus(status=factory_wire.STATUS_RUNNING)
        if self.run_error:
            return RunStatus(status=factory_wire.STATUS_ERROR, detail=self.run_error)
        index = run_key.rsplit("fake", 1)[-1].lstrip("0") or "1"
        return RunStatus(
            status=factory_wire.STATUS_FINISHED,
            result=self.pr_url_template.format(n=index),
        )

    def cancel(self, run_key: str) -> None:
        self.cancelled.append(run_key)


class FakeMachineFactory:
    """Sandbox guids and a container that stops when told, instead of Docker.

    The mirror of ``FakeCloudFactory`` at the same seam, so MachineFactory's order
    loop and its reaping of lost sandboxes can be tested without a daemon. Unlike
    the cloud, the run key arrives on the order: the manager mints it so the hash
    exists before the sandbox reads it.
    """

    def __init__(self, *, startup_error: str = "", retryable: bool = False) -> None:
        self.startup_error = startup_error
        self.retryable = retryable
        self.launches: list[dict[str, Any]] = []
        self.cancelled: list[str] = []
        self.released: list[str] = []
        self.running: set[str] = set()
        self._seq = 0

    def launch(self, order: Any) -> RunHandle:
        if self.startup_error:
            raise AgentStartupError(self.startup_error, retryable=self.retryable)
        self._seq += 1
        run_key = str(order.get("run_key") or f"fake-guid-{self._seq:03d}")
        container = f"mf-{order['repo']}-ticket-{order['ticket_name']}".lower()
        self.launches.append(
            {
                "run_key": run_key,
                "repo": str(order["repo"]),
                "ticket_name": str(order["ticket_name"]),
                "URL": str(order.get("URL") or order.get("repo_url") or ""),
                "auto_pr": bool(order.get("auto_pr", True)),
                "ticket_id": str(order["ticket_id"]),
                "container": container,
            }
        )
        self.running.add(run_key)
        return RunHandle(run_key=run_key, run_id=container)

    def release(self, run_key: str) -> None:
        """No-op stand-in for dropping a Redis sidecar after a run ends."""
        self.released.append(run_key)

    def stop(self, run_key: str) -> None:
        """Make a sandbox vanish the way a crashed container does: silently."""
        self.running.discard(run_key)

    def poll(self, run_key: str) -> RunStatus:
        if run_key in self.running:
            return RunStatus(status=factory_wire.STATUS_RUNNING)
        return RunStatus(
            status=factory_wire.STATUS_FINISHED,
            detail="sandbox is no longer running",
        )

    def cancel(self, run_key: str) -> None:
        self.cancelled.append(run_key)
        self.running.discard(run_key)
