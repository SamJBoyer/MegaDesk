"""WorkDispatcher sequencer: two columns, drag onto the second, expose the order."""

from __future__ import annotations

import pytest
from work_dispatcher_app import IssueTicket, WorkDispatcher, read_sequence

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


@pytest.mark.canvas
def test_dropping_tickets_into_the_sequence_exposes_widget_order(
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

    gate.drop("sequence_scroll", 42)
    gate.drop("sequence_scroll", 41)
    gate.drop("sequence_scroll", 43)
    assert read_sequence(gate.tag_prefix) == [42, 41, 43]
    assert gate.exists("seq_btn_42")
    assert gate.exists("seq_row_41")
    assert not gate.exists("sequence_hint")
    # The tagged list stays put; sequencing copies, it does not move.
    assert gate.exists("ticket_btn_42")

    gate.drop("seq_row_42", 43)
    assert read_sequence(gate.tag_prefix) == [43, 42, 41]
    assert gate.user_data("sequence_scroll") == [43, 42, 41]
