"""The wire format itself, independent of any GUI.

MergeManager and MissionControl each ship their own top-level ``redis_packets``
module. Both are installed as top-level modules in the same environment, so
``import redis_packets`` resolves to whichever editable finder was registered
first — today MergeManager's, by alphabetical .pth order. Two copies of a
contract that both sides of a stream depend on is exactly the seam that drifts
silently, so these tests pin them together.

The parsers accept aliases (``REPO``, ``ticket``, ``workpath``) for
backwards-compatibility, but every writer emits canonical names only. Tests
assert the canonical set so a writer drifting to an alias fails here.
"""

from __future__ import annotations

import pytest
from conftest import FINISHED_CANONICAL_FIELDS, WORKORDER_CANONICAL_FIELDS

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


def test_workorder_writer_emits_only_canonical_fields(mm_wire) -> None:
    fields = mm_wire.workorder_fields(**WORKORDER_SAMPLE)
    assert set(fields) == set(WORKORDER_CANONICAL_FIELDS)
    assert all(isinstance(v, str) for v in fields.values()), "Redis takes strings only"


def test_finished_writer_emits_only_canonical_fields(mc_wire) -> None:
    fields = mc_wire.finished_fields(**FINISHED_SAMPLE)
    assert set(fields) == set(FINISHED_CANONICAL_FIELDS)
    assert all(isinstance(v, str) for v in fields.values())


def test_both_node_copies_build_identical_payloads(mm_wire, mc_wire) -> None:
    """The duplicated contract must not drift between its two copies."""
    assert mm_wire.workorder_fields(**WORKORDER_SAMPLE) == mc_wire.workorder_fields(
        **WORKORDER_SAMPLE
    )
    assert mm_wire.finished_fields(**FINISHED_SAMPLE) == mc_wire.finished_fields(
        **FINISHED_SAMPLE
    )
    assert mm_wire.WORKORDER_STREAM == mc_wire.WORKORDER_STREAM
    assert mm_wire.FINISHED_PREFIX == mc_wire.FINISHED_PREFIX
    assert mm_wire.finished_stream("widgets") == mc_wire.finished_stream("widgets")


def test_both_node_copies_parse_identically(mm_wire, mc_wire) -> None:
    written = mm_wire.workorder_fields(**WORKORDER_SAMPLE)
    assert mm_wire.parse_workorder(written) == mc_wire.parse_workorder(written)

    finished = mc_wire.finished_fields(**FINISHED_SAMPLE)
    assert mm_wire.parse_finished(finished) == mc_wire.parse_finished(finished)


def test_workorder_round_trips_through_the_parser(mm_wire) -> None:
    parsed = mm_wire.parse_workorder(mm_wire.workorder_fields(**WORKORDER_SAMPLE))
    assert parsed["repo"] == "widgets"
    assert parsed["new_wt"] is True
    assert parsed["wt"] == ""
    assert parsed["model"] == "grok-4.5"


def test_conflict_workorder_requires_an_existing_worktree(mm_wire) -> None:
    """new_wt=false is meaningless without a path to work in."""
    with pytest.raises(ValueError):
        mm_wire.workorder_fields(
            repo="widgets",
            url="",
            new_wt=False,
            wt="",
            ticket_name="merge-add-widget-tests",
            instructions="Resolve the conflicts.",
        )


def test_finished_rejects_incomplete_entries(mc_wire) -> None:
    with pytest.raises(ValueError):
        mc_wire.finished_fields(
            ticket_name="add-widget-tests", ticket_id="1-0", wt="", agent_dir=""
        )


def test_merge_instructions_carry_both_absolute_paths(mm_wire) -> None:
    text = mm_wire.merge_workorder_instructions(
        repo="widgets",
        wt=FINISHED_SAMPLE["wt"],
        agent_dir=FINISHED_SAMPLE["agent_dir"],
        ticket_name="add-widget-tests",
    )
    assert FINISHED_SAMPLE["wt"] in text
    assert FINISHED_SAMPLE["agent_dir"] in text
    assert "new_wt is false" in text
