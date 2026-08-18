"""The WORKORDER / FINISHED wire format itself, independent of any GUI.

TicketDispatcher, MachineFactory and MergeManager all write to this family, and
all three import it from ``megadesk_contracts.wire.machine``. There used to be a
copy of it inside two node packages, and tests here pinned the copies together;
now there is one definition and these tests pin its writers and parsers instead.

The parsers accept aliases (``REPO``, ``ticket``, ``workpath``) for
backwards-compatibility, but every writer emits canonical names only. Tests
assert the canonical set so a writer drifting to an alias fails here.
"""

from __future__ import annotations

import pytest
from conftest import (
    FINISHED_CANONICAL_FIELDS,
    GRAPHEVENT_CANONICAL_FIELDS,
    GRAPHRUN_CANONICAL_FIELDS,
    WORKORDER_CANONICAL_FIELDS,
)

WORKORDER_SAMPLE = {
    "repo": "widgets",
    "url": "https://github.com/acme/widgets",
    "new_wt": True,
    "ticket_name": "add-widget-tests",
    "instructions": "Cover the widget module with tests.",
    "model": "grok-4.5",
}
FINISHED_SAMPLE = {
    "ticket_name": "add-widget-tests",
    "ticket_id": "1700000000000-0",
    "wt": r"C:\Floor\widgets\wt\tickets\add-widget-tests",
    "agent_dir": r"C:\Floor\widgets\wt\agents",
}


def test_workorder_writer_emits_only_canonical_fields(machine_wire) -> None:
    fields = machine_wire.workorder_fields(**WORKORDER_SAMPLE)
    assert set(fields) == set(WORKORDER_CANONICAL_FIELDS)
    assert all(isinstance(v, str) for v in fields.values()), "Redis takes strings only"


def test_finished_writer_emits_only_canonical_fields(machine_wire) -> None:
    fields = machine_wire.finished_fields(**FINISHED_SAMPLE)
    assert set(fields) == set(FINISHED_CANONICAL_FIELDS)
    assert all(isinstance(v, str) for v in fields.values())


def test_every_writer_shares_one_definition() -> None:
    """The three nodes on this stream family must import the same module.

    This is what replaced the old copy-versus-copy comparison: sameness is now
    an import fact rather than something a test has to keep checking.
    """
    import merge_manager_app
    import ticket_dispatcher_app
    from megadesk_contracts.wire import cloud, machine

    assert ticket_dispatcher_app.WORKORDER_STREAM == machine.WORKORDER_STREAM
    assert ticket_dispatcher_app.workorder_fields is machine.workorder_fields
    assert ticket_dispatcher_app.CLOUDORDER_STREAM == cloud.CLOUDORDER_STREAM
    assert ticket_dispatcher_app.cloudorder_fields is cloud.cloudorder_fields
    assert merge_manager_app.FINISHED_PREFIX == machine.FINISHED_PREFIX
    assert merge_manager_app.workorder_fields is machine.workorder_fields


def test_workorder_round_trips_through_the_parser(machine_wire) -> None:
    parsed = machine_wire.parse_workorder(machine_wire.workorder_fields(**WORKORDER_SAMPLE))
    assert parsed["repo"] == "widgets"
    assert parsed["new_wt"] is True
    assert parsed["wt"] == ""
    assert parsed["model"] == "grok-4.5"


def test_finished_round_trips_through_the_parser(machine_wire) -> None:
    parsed = machine_wire.parse_finished(machine_wire.finished_fields(**FINISHED_SAMPLE))
    assert parsed["ticket_name"] == FINISHED_SAMPLE["ticket_name"]
    assert parsed["wt"] == FINISHED_SAMPLE["wt"]
    assert parsed["agent_dir"] == FINISHED_SAMPLE["agent_dir"]


def test_conflict_workorder_requires_an_existing_worktree(machine_wire) -> None:
    """new_wt=false is meaningless without a path to work in."""
    with pytest.raises(ValueError):
        machine_wire.workorder_fields(
            repo="widgets",
            url="",
            new_wt=False,
            wt="",
            ticket_name="merge-add-widget-tests",
            instructions="Resolve the conflicts.",
        )


def test_finished_rejects_incomplete_entries(machine_wire) -> None:
    with pytest.raises(ValueError):
        machine_wire.finished_fields(
            ticket_name="add-widget-tests", ticket_id="1-0", wt="", agent_dir=""
        )


def test_agent_handler_rejects_a_status_outside_the_shared_vocabulary(
    machine_wire,
) -> None:
    """Both factories report into one status set, so a typo must not reach Redis."""
    with pytest.raises(ValueError):
        machine_wire.agent_handler_fields(ticket_id="1-0", status="almost-done")


def test_merge_instructions_carry_both_absolute_paths(machine_wire) -> None:
    text = machine_wire.merge_workorder_instructions(
        repo="widgets",
        wt=FINISHED_SAMPLE["wt"],
        agent_dir=FINISHED_SAMPLE["agent_dir"],
        ticket_name="add-widget-tests",
    )
    assert FINISHED_SAMPLE["wt"] in text
    assert FINISHED_SAMPLE["agent_dir"] in text
    assert "new_wt is false" in text


GRAPHRUN_SAMPLE = {
    "guid": "run-1",
    "graph": "work",
    "spec": "",
    "nodes": "",
    "status": "running",
    "ticket_id": "1700000000000-0",
    "ticket_name": "add-widget-tests",
    "repo": "widgets",
    "current": "workhorse_node",
    "started": "2026-08-18T18:00:00+00:00",
    "updated": "2026-08-18T18:01:00+00:00",
    "error": "",
}

GRAPHEVENT_SAMPLE = {
    "guid": "run-1",
    "graph": "work",
    "node": "workhorse_node",
    "status": "running",
    "detail": "ticket work",
    "ts": "2026-08-18T18:01:00+00:00",
}


def test_graph_run_writer_emits_only_canonical_fields(graph_wire) -> None:
    spec = graph_wire.encode_spec(graph_wire.WORK_GRAPH)
    nodes = graph_wire.encode_nodes(graph_wire.initial_nodes(graph_wire.WORK_GRAPH))
    fields = graph_wire.graph_run_fields(
        **{**GRAPHRUN_SAMPLE, "spec": spec, "nodes": nodes}
    )
    assert set(fields) == set(GRAPHRUN_CANONICAL_FIELDS)
    assert all(isinstance(v, str) for v in fields.values())


def test_graph_event_writer_emits_only_canonical_fields(graph_wire) -> None:
    fields = graph_wire.graph_event_fields(**GRAPHEVENT_SAMPLE)
    assert set(fields) == set(GRAPHEVENT_CANONICAL_FIELDS)
    assert all(isinstance(v, str) for v in fields.values())


def test_graph_run_round_trips_through_the_parser(graph_wire) -> None:
    spec = graph_wire.encode_spec(graph_wire.WORK_GRAPH)
    nodes = graph_wire.encode_nodes(graph_wire.initial_nodes(graph_wire.WORK_GRAPH))
    parsed = graph_wire.parse_graph_run(
        graph_wire.graph_run_fields(
            **{**GRAPHRUN_SAMPLE, "spec": spec, "nodes": nodes}
        )
    )
    decoded = graph_wire.decode_spec(parsed["spec"])
    assert decoded.node_names() == graph_wire.WORK_GRAPH.node_names()
    assert decoded.edges == graph_wire.WORK_GRAPH.edges
    assert parsed["current"] == "workhorse_node"
    assert parsed["status"] == "running"


def test_graph_event_round_trips_through_the_parser(graph_wire) -> None:
    parsed = graph_wire.parse_graph_event(
        graph_wire.graph_event_fields(**GRAPHEVENT_SAMPLE)
    )
    assert parsed["node"] == "workhorse_node"
    assert parsed["guid"] == "run-1"


def test_graph_run_rejects_a_status_outside_the_shared_vocabulary(graph_wire) -> None:
    spec = graph_wire.encode_spec(graph_wire.WORK_GRAPH)
    nodes = graph_wire.encode_nodes(graph_wire.initial_nodes(graph_wire.WORK_GRAPH))
    with pytest.raises(ValueError):
        graph_wire.graph_run_fields(
            guid="run-1",
            graph="work",
            spec=spec,
            nodes=nodes,
            status="almost-done",
        )
