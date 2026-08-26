"""AgentHandler work graph: topology, Redis progress, and GraphScope FE.

No Docker and no Cursor API. The SDK is swapped for a fake; repo clone is a
no-op so the graph can run against a tmp workspace.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from conftest import FINISHED_CANONICAL_FIELDS

from megadesk_contracts.agent_audit import AgentAuditLog
from megadesk_contracts.wire import graph as graph_wire
from megadesk_contracts.wire import machine as machine_wire

pytestmark = [pytest.mark.redis]


WORK_NODE_NAMES = (
    "startup_node",
    "pathfinder_node",
    "workhorse_node",
    "git_node",
    "teardown_node",
)

REPO_URL = "https://github.com/acme/widgets"


class NullRepo:
    """Stand-in for ``SandboxRepo``: no clone, no push, no PR."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def prepare(self) -> None:
        return None

    def restore(self) -> None:
        return None

    def publish_branch(self) -> str:
        return ""


class FakeRun:
    def __init__(self, *, status: str = "finished", result: str = "ok") -> None:
        self.id = "run-1"
        self._status = status
        self._result = result

    def messages(self):
        return iter(())

    def wait(self):
        return SimpleNamespace(status=self._status, result=self._result)

    def text(self):
        return self._result


class FakeCursorAgent:
    """Records every prompt. Fails when the prompt contains ``fail_needle``."""

    def __init__(self, recorder: list[str], fail_needle: str = "") -> None:
        self.agent_id = "ag-1"
        self._recorder = recorder
        self._fail_needle = fail_needle

    def __enter__(self) -> "FakeCursorAgent":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def send(self, instruction: str) -> FakeRun:
        self._recorder.append(instruction)
        if self._fail_needle and self._fail_needle in instruction:
            return FakeRun(status="error", result="")
        return FakeRun(status="finished", result="ok")

    @classmethod
    def bind(cls, recorder: list[str], fail_needle: str = "") -> type:
        class Bound(cls):  # type: ignore[valid-type,misc]
            @classmethod
            def create(inner_cls, **kwargs: Any) -> "FakeCursorAgent":
                return FakeCursorAgent(recorder, fail_needle)

        return Bound


def _init_dirty_repo(workspace: Path) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init"],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    )
    (workspace / "dirty.txt").write_text("left for git\n", encoding="utf-8")


def _seed_order(
    redis_client,
    *,
    guid: str,
    workspace: Path,
    ticket_name: str = "add-widget-tests",
) -> str:
    ticket_id = redis_client.xadd(
        machine_wire.WORKORDER_STREAM,
        machine_wire.workorder_fields(
            repo="widgets",
            url=REPO_URL,
            ticket_name=ticket_name,
            instructions="Create harness-smoke.txt with the text ok",
            model="auto",
        ),
    )
    redis_client.hset(
        machine_wire.agent_handler_key(guid),
        mapping=machine_wire.agent_handler_fields(
            ticket_id=str(ticket_id),
            status=machine_wire.STATUS_QUEUED,
        ),
    )
    workspace.mkdir(parents=True, exist_ok=True)
    return str(ticket_id)


def _context(
    redis_client,
    *,
    guid: str,
    workspace: Path,
    audit: AgentAuditLog,
):
    from AgentHandler.graph import GraphReporter, RunContext

    reporter = GraphReporter(
        redis_client,
        guid=guid,
        spec=graph_wire.WORK_GRAPH,
        audit=audit,
        repo="widgets",
        ticket_name="add-widget-tests",
    )
    return RunContext(
        guid=guid,
        redis=redis_client,
        audit=audit,
        reporter=reporter,
        api_key="fake",
        workspace=str(workspace),
        ticket="add-widget-tests",
        repo="widgets",
        repo_url=REPO_URL,
        auto_pr=True,
        env_ticket_id="",
        default_model="auto",
        repo_factory=NullRepo,
    )


def _event_pairs(redis_client, guid: str) -> list[tuple[str, str]]:
    return [
        (event["node"], event["status"])
        for event in graph_wire.read_graph_events(redis_client, guid)
    ]


def test_work_graph_is_the_straight_line_plus_error_edges_to_teardown() -> None:
    from langgraph.graph import END, START

    from AgentHandler.graph import RunContext, build_work_graph

    audit = SimpleNamespace(event=lambda *a, **k: None)
    context = RunContext(
        guid="shape",
        redis=None,
        audit=audit,
        reporter=SimpleNamespace(),
        api_key="fake",
        workspace=".",
        repo_factory=NullRepo,
    )
    compiled = build_work_graph(context)
    graph = compiled.get_graph()
    nodes = {name for name in graph.nodes if name not in {START, END, "__start__", "__end__"}}
    assert nodes == set(WORK_NODE_NAMES)
    edges = {(edge.source, edge.target) for edge in graph.edges}
    assert ("startup_node", "pathfinder_node") in edges
    assert ("pathfinder_node", "workhorse_node") in edges
    assert ("workhorse_node", "git_node") in edges
    assert ("git_node", "teardown_node") in edges
    assert ("startup_node", "teardown_node") in edges
    assert ("pathfinder_node", "teardown_node") in edges
    assert ("workhorse_node", "teardown_node") in edges


@pytest.mark.git
def test_work_graph_happy_path_publishes_finished_and_clears_hashes(
    redis_client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from AgentHandler.graph import run_work_graph

    guid = "wg-happy"
    workspace = tmp_path / "wt"
    _seed_order(redis_client, guid=guid, workspace=workspace)
    _init_dirty_repo(workspace)

    prompts: list[str] = []
    monkeypatch.setattr("AgentHandler.handler.Agent", FakeCursorAgent.bind(prompts))

    audit = AgentAuditLog(guid, path=tmp_path / "audit.md", repo="widgets")
    try:
        final = run_work_graph(_context(redis_client, guid=guid, workspace=workspace, audit=audit))
    finally:
        audit.close()

    assert final.get("status") == machine_wire.STATUS_FINISHED
    assert final.get("exit_code") == 0

    pairs = _event_pairs(redis_client, guid)
    assert pairs[0] == ("startup_node", machine_wire.STATUS_RUNNING)
    assert [node for node, status in pairs if status == machine_wire.STATUS_RUNNING] == list(
        WORK_NODE_NAMES
    )
    assert pairs[-1] == ("teardown_node", machine_wire.STATUS_FINISHED)

    finished = redis_client.xrange(machine_wire.finished_stream("widgets"))
    assert len(finished) == 1
    fields = finished[0][1]
    assert set(fields) == set(FINISHED_CANONICAL_FIELDS)
    assert fields["ticket_name"] == "add-widget-tests"
    assert fields["status"] == machine_wire.STATUS_FINISHED
    assert fields["pr_url"] == ""

    assert redis_client.exists(machine_wire.agent_handler_key(guid)) == 0
    assert redis_client.exists(graph_wire.graph_run_key(guid)) == 0

    joined = "\n".join(prompts)
    assert "Leave your changes in the working tree" in joined
    assert "When you commit your work" not in joined
    assert "Commit the work already present" in joined
    assert "Names the ticket" in joined


def test_work_graph_prepares_repo_on_startup_and_restores_on_teardown(
    redis_client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from AgentHandler.graph import run_work_graph

    guid = "wg-rectify"
    workspace = tmp_path / "wt"
    _seed_order(redis_client, guid=guid, workspace=workspace)

    calls: list[str] = []

    class RecordingRepo(NullRepo):
        def prepare(self) -> None:
            calls.append("prepare")

        def restore(self) -> None:
            calls.append("restore")

        def publish_branch(self) -> str:
            calls.append("publish")
            return "https://github.com/acme/widgets/pull/9"

    prompts: list[str] = []
    monkeypatch.setattr("AgentHandler.handler.Agent", FakeCursorAgent.bind(prompts))
    audit = AgentAuditLog(guid, path=tmp_path / "audit.md", repo="widgets")
    context = _context(redis_client, guid=guid, workspace=workspace, audit=audit)
    context.repo_factory = RecordingRepo
    try:
        final = run_work_graph(context)
    finally:
        audit.close()

    assert final.get("status") == machine_wire.STATUS_FINISHED
    assert calls[0] == "prepare"
    assert "publish" in calls
    assert "restore" in calls
    assert final.get("pr_url") == "https://github.com/acme/widgets/pull/9"

    finished = redis_client.xrange(machine_wire.finished_stream("widgets"))
    assert len(finished) == 1
    assert finished[0][1]["status"] == machine_wire.STATUS_FINISHED
    assert finished[0][1]["pr_url"] == "https://github.com/acme/widgets/pull/9"

    details = [
        event["detail"]
        for event in graph_wire.read_graph_events(redis_client, guid)
        if event["node"] == "teardown_node" and event["status"] == machine_wire.STATUS_FINISHED
    ]
    assert details
    assert "published FINISHED" in details[-1]
    assert "pr=" in details[-1]


def test_work_graph_failure_still_reaches_teardown(
    redis_client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from AgentHandler.graph import run_work_graph

    guid = "wg-fail"
    workspace = tmp_path / "wt"
    _seed_order(redis_client, guid=guid, workspace=workspace)

    prompts: list[str] = []
    monkeypatch.setattr(
        "AgentHandler.handler.Agent",
        FakeCursorAgent.bind(prompts, fail_needle="Leave your changes"),
    )

    audit = AgentAuditLog(guid, path=tmp_path / "audit.md", repo="widgets")
    try:
        final = run_work_graph(_context(redis_client, guid=guid, workspace=workspace, audit=audit))
    finally:
        audit.close()

    assert final.get("status") == machine_wire.STATUS_ERROR
    assert final.get("exit_code") == 1
    assert final.get("failed_node") == "workhorse_node"

    pairs = _event_pairs(redis_client, guid)
    running = [node for node, status in pairs if status == machine_wire.STATUS_RUNNING]
    assert running == [
        "startup_node",
        "pathfinder_node",
        "workhorse_node",
        "teardown_node",
    ]
    assert ("workhorse_node", machine_wire.STATUS_ERROR) in pairs
    assert ("teardown_node", machine_wire.STATUS_FINISHED) in pairs
    assert not any(node == "git_node" for node, _status in pairs)

    finished = redis_client.xrange(machine_wire.finished_stream("widgets"))
    assert len(finished) == 1
    fields = finished[0][1]
    assert set(fields) == set(FINISHED_CANONICAL_FIELDS)
    assert fields["status"] == machine_wire.STATUS_ERROR
    assert fields["pr_url"] == ""
    assert redis_client.exists(machine_wire.agent_handler_key(guid)) == 0
    assert redis_client.exists(graph_wire.graph_run_key(guid)) == 0


@pytest.mark.canvas
def test_graph_scope_draws_an_injected_run(harness, redis_client, tmp_path: Path) -> None:
    spec = graph_wire.encode_spec(graph_wire.WORK_GRAPH)
    nodes = graph_wire.initial_nodes(graph_wire.WORK_GRAPH)
    nodes["startup_node"] = graph_wire.node_progress(status=machine_wire.STATUS_FINISHED)
    nodes["pathfinder_node"] = graph_wire.node_progress(status=machine_wire.STATUS_RUNNING)
    guid = "scope-live"
    redis_client.hset(
        graph_wire.graph_run_key(guid),
        mapping=graph_wire.graph_run_fields(
            guid=guid,
            graph=graph_wire.WORK_GRAPH.name,
            spec=spec,
            nodes=graph_wire.encode_nodes(nodes),
            status=machine_wire.STATUS_RUNNING,
            ticket_id="1-0",
            ticket_name="add-widget-tests",
            repo="widgets",
            current="pathfinder_node",
        ),
    )
    redis_client.xadd(
        graph_wire.GRAPHEVENT_STREAM,
        graph_wire.graph_event_fields(
            guid=guid,
            graph=graph_wire.WORK_GRAPH.name,
            node="pathfinder_node",
            status=machine_wire.STATUS_RUNNING,
        ),
    )

    scope = harness.drop("graph_scope")
    harness.wait_until(
        lambda: "live=1" in (scope.get("status_lbl") or ""),
        message="GraphScope to show the injected GRAPHRUN",
    )
    labels = scope.get("graph_nodes")
    for name in WORK_NODE_NAMES:
        assert name in labels
    items = scope.items("run_list")
    assert any("running" in item for item in items)
    assert scope.exists("graph_dl")
    assert scope.exists("box_startup_node")
    harness.screenshot("graph-scope-live")
