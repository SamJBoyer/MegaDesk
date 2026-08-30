"""The WORKORDER / FINISHED wire format itself, independent of any GUI.

WorkDispatcher and MachineFactory write to this family, and both import it
from ``megadesk_contracts.wire.machine``. Tests assert the canonical field set
so a writer drifting off it fails here.
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
    "ticket_name": "add-widget-tests",
    "instructions": "Cover the widget module with tests.",
    "model": "grok-4.5",
    "auto_pr": True,
}
FINISHED_SAMPLE = {
    "ticket_name": "add-widget-tests",
    "ticket_id": "1700000000000-0",
    "status": "finished",
    "pr_url": "https://github.com/acme/widgets/pull/7",
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
    """The nodes on this stream family must import the same module.

    This is what replaced the old copy-versus-copy comparison: sameness is now
    an import fact rather than something a test has to keep checking.
    """
    import work_dispatcher_app
    from megadesk_contracts.wire import cloud, machine

    assert work_dispatcher_app.WORKORDER_CHANNEL == machine.WORKORDER_CHANNEL
    assert work_dispatcher_app.workorder_fields is machine.workorder_fields
    assert work_dispatcher_app.publish_workorder is machine.publish_workorder
    assert work_dispatcher_app.CLOUDORDER_CHANNEL == cloud.CLOUDORDER_CHANNEL
    assert work_dispatcher_app.cloudorder_fields is cloud.cloudorder_fields
    assert work_dispatcher_app.publish_cloudorder is cloud.publish_cloudorder


def test_workorder_signal_round_trips_through_json(machine_wire) -> None:
    from megadesk_contracts.wire.signal import decode_fields, encode_fields

    fields = machine_wire.workorder_fields(**WORKORDER_SAMPLE)
    assert decode_fields(encode_fields(fields)) == fields


def test_workorder_round_trips_through_the_parser(machine_wire) -> None:
    parsed = machine_wire.parse_workorder(machine_wire.workorder_fields(**WORKORDER_SAMPLE))
    assert parsed["repo"] == "widgets"
    assert parsed["auto_pr"] is True
    assert parsed["URL"] == "https://github.com/acme/widgets"
    assert parsed["model"] == "grok-4.5"
    assert parsed["pictures"] == []


def test_prompt_payload_stays_a_string_without_pictures() -> None:
    from megadesk_contracts.factory import prompt_payload

    assert prompt_payload("do the work") == "do the work"


def test_pictures_round_trip_on_both_order_families(machine_wire) -> None:
    from megadesk_contracts.human_gate import extract_issue_pictures
    from megadesk_contracts.wire import cloud

    urls = [
        "https://github.com/user-attachments/assets/aaaa",
        "https://example.com/mock.png",
    ]
    body = (
        "See ![one](https://github.com/user-attachments/assets/aaaa) "
        "and <img src=\"https://example.com/mock.png\">."
    )
    assert extract_issue_pictures(body) == urls

    machine = machine_wire.parse_workorder(
        machine_wire.workorder_fields(**WORKORDER_SAMPLE, pictures=urls)
    )
    assert machine["pictures"] == urls

    cloud_parsed = cloud.parse_cloudorder(
        cloud.cloudorder_fields(
            order_id="order-1",
            repo_url="https://github.com/acme/widgets",
            title="add-widget-tests",
            instructions="Cover the widget module.",
            pictures=urls,
        )
    )
    assert cloud_parsed["pictures"] == urls
    assert extract_issue_pictures("no images here") == []


def test_workorder_parser_accepts_a_legacy_entry_without_pictures(machine_wire) -> None:
    parsed = machine_wire.parse_workorder(
        {
            "repo": "widgets",
            "URL": "https://github.com/acme/widgets",
            "ticket_name": "add-widget-tests",
            "instructions": "Cover the widget module.",
            "model": "auto",
            "auto_pr": "true",
        }
    )
    assert parsed["pictures"] == []
    assert parsed["issue"] == ""


def test_finished_round_trips_through_the_parser(machine_wire) -> None:
    parsed = machine_wire.parse_finished(machine_wire.finished_fields(**FINISHED_SAMPLE))
    assert parsed["ticket_name"] == FINISHED_SAMPLE["ticket_name"]
    assert parsed["status"] == FINISHED_SAMPLE["status"]
    assert parsed["pr_url"] == FINISHED_SAMPLE["pr_url"]


def test_workorder_parser_requires_canonical_field_names(machine_wire) -> None:
    with pytest.raises(ValueError, match="repo, URL, ticket_name, instructions"):
        machine_wire.parse_workorder(
            {
                "REPO": "widgets",
                "ticket": "add-widget-tests",
                "prompt": "Cover the widget module.",
                "auto_pr": "true",
            }
        )


def test_finished_parser_requires_canonical_field_names(machine_wire) -> None:
    with pytest.raises(ValueError):
        machine_wire.parse_finished(
            {
                "ticket": FINISHED_SAMPLE["ticket_name"],
                "ticket_id": FINISHED_SAMPLE["ticket_id"],
                "state": FINISHED_SAMPLE["status"],
                "url": FINISHED_SAMPLE["pr_url"],
            }
        )


def test_finished_rejects_incomplete_entries(machine_wire) -> None:
    with pytest.raises(ValueError):
        machine_wire.finished_fields(
            ticket_name="add-widget-tests", ticket_id="1-0", status=""
        )


def test_agent_handler_rejects_a_status_outside_the_shared_vocabulary(
    machine_wire,
) -> None:
    """Both factories report into one status set, so a typo must not reach Redis."""
    with pytest.raises(ValueError):
        machine_wire.agent_handler_fields(ticket_id="1-0", status="almost-done")


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
