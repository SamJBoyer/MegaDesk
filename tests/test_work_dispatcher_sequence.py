"""WorkDispatcher sequencer: two columns, drag onto the second, expose the order."""

from __future__ import annotations

from megadesk_contracts import host as dpg
import pytest
from megadesk_contracts.human_gate import (
    LABEL_AGENT_READY,
    LABEL_IN_PROGRESS,
    relabel_issue,
)
from megadesk_contracts.testing import FakeGh
from work_dispatcher_app import (
    TICKET_H,
    IssueTicket,
    WorkDispatcher,
    read_sequence,
    resolve_model,
)

REPO_URL = "https://github.com/acme/widgets"


def test_sequence_ids_tracks_place_and_reorder() -> None:
    """The order lives on the instance even before any widgets exist."""
    dispatcher = WorkDispatcher()
    dispatcher._tickets = {
        1: IssueTicket(1, "a", "", ""),
        2: IssueTicket(2, "b", "", ""),
        3: IssueTicket(3, "c", "", ""),
    }
    dispatcher._place_in_sequence(1)
    dispatcher._place_in_sequence(2)
    dispatcher._place_in_sequence(3)
    assert dispatcher.sequence_ids() == [1, 2, 3]

    dispatcher._place_in_sequence(3, before_id=1)
    assert dispatcher.sequence_ids() == [3, 1, 2]

    dispatcher._place_in_sequence(1)
    assert dispatcher.sequence_ids() == [3, 2, 1]


def test_model_levels_map_onto_wire_ids() -> None:
    assert resolve_model("low") == "composer-2.5"
    assert resolve_model("medium") == "grok-4.6"
    assert resolve_model("high") == "claude-opus-5"
    assert resolve_model("opus-5") == "claude-opus-5"
    assert resolve_model("grok-4.6") == "grok-4.6"


def test_a_drag_does_not_dispatch_the_ticket() -> None:
    dispatcher = WorkDispatcher()
    dispatcher._tickets = {1: IssueTicket(1, "a", "", "")}
    dispatcher._drag_active = True
    dispatcher._on_ticket_pressed(None, None, 1)
    assert 1 not in dispatcher._dispatched


def test_relabel_issue_swaps_agent_ready_for_in_progress() -> None:
    gh = FakeGh()
    gh.add_issue(41, "x")
    ok, err = relabel_issue("acme", "widgets", 41, gh=gh)
    assert ok and not err
    assert LABEL_IN_PROGRESS in gh.issues[0].labels
    assert LABEL_AGENT_READY not in gh.issues[0].labels


def _parent_alias(tag: str | int) -> str:
    parent = dpg.get_item_parent(tag)
    return str(dpg.get_item_alias(parent) or parent)


@pytest.mark.canvas
def test_dropping_tickets_into_the_sequence_moves_the_whole_row(
    fake_gh, harness
) -> None:
    """Drop callbacks on the sequence column are the production drag path."""
    fake_gh.add_issue(41, "first-ticket", "Do the first thing.")
    fake_gh.add_issue(42, "second-ticket", "Do the second thing.")
    fake_gh.add_issue(43, "third-ticket", "Do the third thing.")

    gate = harness.drop("work_dispatcher")
    gate.type_into("git_url", REPO_URL)
    harness.wait_for_widget(gate, "ticket_btn_41")
    harness.wait_for_widget(gate, "ticket_btn_42")
    harness.wait_for_widget(gate, "ticket_btn_43")

    assert gate.exists("sequence_scroll")
    assert gate.exists("ticket_scroll")
    assert read_sequence(gate.tag_prefix) == []
    assert gate.exists("sequence_hint")

    gate.select("ticket_factory_42", "cloud")
    gate.select("ticket_model_42", "high")
    gate.drop("sequence_scroll", 42)
    gate.drop("sequence_scroll", 41)
    gate.drop("sequence_scroll", 43)
    assert read_sequence(gate.tag_prefix) == [42, 41, 43]
    assert gate.exists("ticket_btn_42")
    assert gate.exists("ticket_row_41")
    assert gate.exists("ticket_factory_42")
    assert gate.exists("ticket_model_42")
    assert gate.get("ticket_factory_42") == "cloud"
    assert gate.get("ticket_model_42") == "high"
    assert not gate.exists("sequence_hint")
    assert _parent_alias(gate.require("ticket_row_42")) == gate.tag("sequence_scroll")

    gate.drop("ticket_row_42", 43)
    assert read_sequence(gate.tag_prefix) == [43, 42, 41]
    assert gate.user_data("sequence_scroll") == [43, 42, 41]


@pytest.mark.canvas
def test_a_mouse_drag_from_the_factory_combo_moves_the_ticket(
    fake_gh, harness, monkeypatch
) -> None:
    """Combos are not DPG drag sources; the mouse-threshold path has to cover them."""
    from work_dispatcher_app import _LIVE

    fake_gh.add_issue(42, "second-ticket", "Do the second thing.")
    gate = harness.drop("work_dispatcher")
    gate.type_into("git_url", REPO_URL)
    harness.wait_for_widget(gate, "ticket_factory_42")
    inst = _LIVE[gate.tag_prefix]
    hovered = [gate.tag("ticket_factory_42")]
    monkeypatch.setattr(
        "work_dispatcher_app.dpg.is_item_hovered",
        lambda item, **kwargs: str(dpg.get_item_alias(item) or item) in hovered
        or str(item) in hovered,
    )
    inst._on_mouse_drag(None, None)
    assert inst._drag_ticket == 42
    assert inst._drag_active

    hovered[:] = [gate.tag("sequence_scroll")]
    inst._on_mouse_release(None, None)
    assert read_sequence(gate.tag_prefix) == [42]
    assert _parent_alias(gate.require("ticket_row_42")) == gate.tag("sequence_scroll")
    assert gate.get("ticket_factory_42") == "machine"


@pytest.mark.canvas
def test_depth_extrudes_the_columns_to_fit_tickets(harness) -> None:
    gate = harness.drop("work_dispatcher")
    start = dpg.get_item_configuration(gate.require("ticket_scroll"))["height"]
    assert start == 2 * TICKET_H

    gate.set("max_depth", 5)
    gate.fire("max_depth", 5)

    assert dpg.get_item_configuration(gate.require("ticket_scroll"))["height"] == 5 * TICKET_H
    assert dpg.get_item_configuration(gate.require("sequence_scroll"))["height"] == 5 * TICKET_H
    content_h = dpg.get_item_configuration(f"{gate.tag_prefix}::content")["height"]
    assert content_h >= 5 * TICKET_H
