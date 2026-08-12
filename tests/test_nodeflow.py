"""The node-to-node workflow: TicketDispatcher → MissionControl → MergeManager.

Each test crosses a seam that unit tests on either side cannot reach — a Redis
stream or a GUI callback. The chain is cut at the sandbox boundary: `FakeAgent`
stands in for Floor cloning, Docker and `cursor_sdk`, while the GUI, the stream
contracts, the consumer-group semantics and the real `git merge` stay real.

The real MissionControl BE is never launched. Dropping a node only publishes a
LAUNCHREQUEST when Supervisor is alive, and neither FE under test has a BE, so
nothing here can spawn Docker.

Scenario numbers match the table in `Docs/integration_testing.md`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import (
    FINISHED_CANONICAL_FIELDS,
    FINISHED_GROUP,
    WORKORDER_CANONICAL_FIELDS,
    WORKORDER_STREAM,
)

pytestmark = [pytest.mark.canvas, pytest.mark.redis, pytest.mark.git]

REPO_URL = "https://github.com/acme/widgets"
REPO = "widgets"


# --- helpers ---------------------------------------------------------------


def connect_dispatcher(harness, url: str = REPO_URL):
    """Drop TicketDispatcher and point it at a repo, as an operator would."""
    dispatcher = harness.drop("ticket_dispatcher")
    dispatcher.type_into("git_url", url)
    return dispatcher


def dispatch(harness, dispatcher, issue_id: int, *, model: str | None = None) -> None:
    """Wait for the issue row to appear, optionally pick a model, then press it."""
    harness.wait_for_widget(dispatcher, f"ticket_btn_{issue_id}")
    if model is not None:
        dispatcher.select(f"ticket_model_{issue_id}", model)
    dispatcher.click(f"ticket_btn_{issue_id}")


def seed_finished(redis_client, wire, floor, *, ticket_name: str, wt: Path) -> str:
    """XADD a FINISHED entry the way MissionControl would, and return its id."""
    return str(
        redis_client.xadd(
            wire.finished_stream(floor.repo),
            wire.finished_fields(
                ticket_name=ticket_name,
                ticket_id="0-1",
                wt=str(Path(wt).resolve()),
                agent_dir=str(floor.agents_dir.resolve()),
            ),
        )
    )


def merge_row(harness, redis_client, wire, floor, *, ticket_name: str, wt: Path):
    """Seed a FINISHED entry, host MergeManager, and wait for its row."""
    entry_id = seed_finished(
        redis_client, wire, floor, ticket_name=ticket_name, wt=wt
    )
    manager = harness.drop("merge_manager")
    key = f"{floor.repo}|{entry_id}"
    harness.wait_for_widget(manager, f"name::{key}")
    return manager, key, entry_id


# --- T1 / T2: TicketDispatcher → WORKORDER ---------------------------------


def test_t1_dispatch_writes_the_canonical_workorder(
    redis_client, fake_gh, harness, workorders
) -> None:
    """Catches field renames and legacy WORKREQUEST / REPO drift."""
    fake_gh.add_issue(41, "add-widget-tests", "Cover the widget module with tests.")

    dispatcher = connect_dispatcher(harness)
    dispatch(harness, dispatcher, 41)

    entries = workorders()
    assert len(entries) == 1, f"expected one WORKORDER, got {entries}"
    _entry_id, fields = entries[0]

    assert set(fields) == set(WORKORDER_CANONICAL_FIELDS), (
        "WORKORDER must carry exactly the canonical field names; "
        f"got {sorted(fields)}"
    )
    assert fields["repo"] == REPO
    assert fields["URL"] == REPO_URL
    assert fields["new_wt"] == "true"
    assert fields["wt"] == ""
    assert fields["ticket_name"] == "add-widget-tests"
    assert fields["instructions"] == "Cover the widget module with tests."
    assert fields["model"] == "auto"


def test_t1b_issue_without_a_body_falls_back_to_its_title(
    redis_client, fake_gh, harness, workorders
) -> None:
    fake_gh.add_issue(7, "bodyless-ticket", "")

    dispatcher = connect_dispatcher(harness)
    dispatch(harness, dispatcher, 7)

    _entry_id, fields = workorders()[0]
    assert fields["instructions"] == "bodyless-ticket"


def test_t2_per_row_model_combo_reaches_the_payload(
    redis_client, fake_gh, harness, workorders
) -> None:
    """Catches per-row widget → payload wiring."""
    fake_gh.add_issue(42, "pick-a-model", "Use the fast model.")

    dispatcher = connect_dispatcher(harness)
    dispatch(harness, dispatcher, 42, model="grok-4.5")

    _entry_id, fields = workorders()[0]
    assert fields["model"] == "grok-4.5"


def test_t2b_gh_failure_surfaces_in_the_status_widget(
    redis_client, fake_gh, harness
) -> None:
    fake_gh.repo_error = "could not resolve to a Repository"

    dispatcher = connect_dispatcher(harness)

    harness.wait_until(
        lambda: fake_gh.repo_error in dispatcher.get("status_text"),
        message="the gh error to reach the status widget",
    )
    assert not dispatcher.exists("ticket_btn_1")


# --- T3: WORKORDER → sandbox boundary → FINISHED ---------------------------


def test_t3_agent_acks_the_group_and_publishes_finished(
    redis_client, fake_gh, harness, git_floor, fake_agent, mc_wire
) -> None:
    """Catches consumer-group and ack semantics, and path relativization."""
    fake_gh.add_issue(43, "t3-ticket", "Make a commit.")

    dispatcher = connect_dispatcher(harness)
    dispatch(harness, dispatcher, 43)

    runs = fake_agent.run_once()
    assert len(runs) == 1, "the mission_control group delivered nothing"
    run = runs[0]
    assert run.repo == REPO
    assert run.new_wt is True
    assert fake_agent.pending() == 0, "WORKORDER entries left unacked after handling"

    finished = redis_client.xrange(mc_wire.finished_stream(REPO))
    assert len(finished) == 1
    _finished_id, fields = finished[0]

    assert set(fields) == set(FINISHED_CANONICAL_FIELDS), (
        f"FINISHED must carry exactly the canonical fields; got {sorted(fields)}"
    )
    assert fields["ticket_name"] == "t3-ticket"
    assert fields["ticket_id"] == run.workorder_id

    wt, agent_dir = Path(fields["wt"]), Path(fields["agent_dir"])
    assert wt.is_absolute() and agent_dir.is_absolute(), "paths must be host-absolute"
    assert wt.is_dir() and agent_dir.is_dir()
    assert agent_dir == git_floor.agents_dir.resolve()
    assert git_floor.current_branch(wt) == "ticket/t3-ticket"


def test_t3b_a_second_pass_delivers_nothing_new(
    redis_client, fake_gh, harness, fake_agent, mc_wire
) -> None:
    """An acked entry must not be redelivered on the next poll."""
    fake_gh.add_issue(44, "t3b-ticket", "Once only.")

    dispatcher = connect_dispatcher(harness)
    dispatch(harness, dispatcher, 44)

    assert len(fake_agent.run_once()) == 1
    assert fake_agent.run_once() == []
    assert redis_client.xlen(mc_wire.finished_stream(REPO)) == 1


# --- T4: FINISHED → MergeManager GUI --------------------------------------


def test_t4_finished_entry_populates_a_merge_row(
    redis_client, harness, git_floor, mc_wire
) -> None:
    """Catches stream → GUI population, which depends on the frame-pump drain."""
    ticket = git_floor.add_ticket("t4-ticket")
    manager, key, entry_id = merge_row(
        harness, redis_client, mc_wire, git_floor, ticket_name="t4-ticket", wt=ticket
    )

    assert manager.get(f"name::{key}") == "t4-ticket"
    assert manager.shown(f"merge::{key}"), "merge must be offered for a pending row"
    assert not manager.shown(f"dismiss::{key}")
    assert not manager.shown(f"reset::{key}")
    assert manager.exists(f"testme::{key}")


def test_t4b_malformed_finished_entry_is_acked_not_shown(
    redis_client, harness, git_floor, mc_wire
) -> None:
    """A bad entry must be acked away, not retried forever or rendered."""
    redis_client.xadd(mc_wire.finished_stream(REPO), {"ticket_name": "broken"})
    manager = harness.drop("merge_manager")

    harness.wait_until(
        lambda: _group_exists(redis_client, mc_wire.finished_stream(REPO)),
        message="MergeManager to create its consumer group",
    )
    harness.wait_until(
        lambda: _pending(redis_client, mc_wire.finished_stream(REPO)) == 0,
        message="the malformed entry to be acked",
    )
    assert manager.suffixes(r"^name::") == []


def _group_exists(redis_client, stream: str) -> bool:
    try:
        groups = redis_client.xinfo_groups(stream)
    except Exception:  # noqa: BLE001 - stream or group not created yet
        return False
    return any(g.get("name") == FINISHED_GROUP for g in groups)


def _pending(redis_client, stream: str) -> int:
    info = redis_client.xpending(stream, FINISHED_GROUP)
    if isinstance(info, dict):
        return int(info.get("pending") or 0)
    return int(info[0]) if info else 0


# --- T5 / T6 / T7: the merge decision -------------------------------------


def test_t5_clean_merge_pushes_and_flips_the_row(
    redis_client, harness, git_floor, mc_wire
) -> None:
    """Catches the merge + push path and the row state machine."""
    ticket = git_floor.add_ticket("t5-ticket")
    sha = git_floor.commit(ticket, "feature.txt", "a feature\n", "ticket: add feature")
    manager, key, _entry_id = merge_row(
        harness, redis_client, mc_wire, git_floor, ticket_name="t5-ticket", wt=ticket
    )

    manager.click(f"merge::{key}")
    harness.pump(2)

    assert git_floor.contains(git_floor.agents_dir, sha), (
        f"ticket commit {sha[:8]} did not land on agents: "
        f"{git_floor.subjects(git_floor.agents_dir)}"
    )
    assert git_floor.origin_sha("agents") == git_floor.head(git_floor.agents_dir), (
        "a successful merge always pushes; origin/agents is behind"
    )
    assert git_floor.is_clean(git_floor.agents_dir)

    assert not manager.shown(f"merge::{key}")
    assert manager.shown(f"dismiss::{key}")
    assert "merged" in manager.get(f"name::{key}")


def test_t5b_dirty_agents_blocks_the_merge_and_offers_hard_reset(
    redis_client, harness, git_floor, mc_wire
) -> None:
    ticket = git_floor.add_ticket("t5b-ticket")
    sha = git_floor.commit(ticket, "feature.txt", "a feature\n")
    git_floor.dirty(git_floor.agents_dir)

    manager, key, _entry_id = merge_row(
        harness, redis_client, mc_wire, git_floor, ticket_name="t5b-ticket", wt=ticket
    )
    manager.click(f"merge::{key}")
    harness.pump(2)

    assert not git_floor.contains(git_floor.agents_dir, sha)
    assert manager.shown(f"reset::{key}")
    assert not manager.shown(f"merge::{key}")

    manager.click(f"reset::{key}")
    harness.pump(2)
    assert git_floor.is_clean(git_floor.agents_dir)
    assert manager.shown(f"merge::{key}"), "after hard-reset the row must be mergeable"

    manager.click(f"merge::{key}")
    harness.pump(2)
    assert git_floor.contains(git_floor.agents_dir, sha)


def test_t6_conflict_aborts_and_publishes_a_followup_workorder(
    redis_client, harness, git_floor, mc_wire, workorders
) -> None:
    """The loop-closing seam: a conflicted merge must re-enter the pipeline."""
    ticket = git_floor.add_ticket("t6-ticket")
    git_floor.make_conflict("t6-ticket")
    agents_before = git_floor.head(git_floor.agents_dir)

    manager, key, _entry_id = merge_row(
        harness, redis_client, mc_wire, git_floor, ticket_name="t6-ticket", wt=ticket
    )
    manager.click(f"merge::{key}")
    harness.pump(2)

    assert git_floor.is_clean(git_floor.agents_dir), "the merge was not aborted"
    assert not git_floor.merge_in_progress(git_floor.agents_dir), "MERGE_HEAD left behind"
    assert git_floor.head(git_floor.agents_dir) == agents_before

    entries = workorders()
    assert len(entries) == 1, f"expected one follow-up WORKORDER, got {entries}"
    _workorder_id, fields = entries[0]

    assert set(fields) == set(WORKORDER_CANONICAL_FIELDS)
    assert fields["repo"] == REPO
    assert fields["new_wt"] == "false"
    assert Path(fields["wt"]) == ticket.resolve()
    assert fields["ticket_name"] == "merge-t6-ticket"
    assert fields["model"] == "auto"
    assert str(ticket.resolve()) in fields["instructions"]

    assert not manager.shown(f"merge::{key}")
    assert manager.shown(f"dismiss::{key}")


def test_t6b_the_followup_workorder_is_consumable(
    redis_client, harness, git_floor, mc_wire, fake_agent, workorders
) -> None:
    """The re-entered WORKORDER must satisfy the consumer that reads it."""
    ticket = git_floor.add_ticket("t6b-ticket")
    git_floor.make_conflict("t6b-ticket")

    manager, key, _entry_id = merge_row(
        harness, redis_client, mc_wire, git_floor, ticket_name="t6b-ticket", wt=ticket
    )
    manager.click(f"merge::{key}")
    harness.pump(2)

    runs = fake_agent.run_once()
    assert len(runs) == 1
    run = runs[0]
    assert run.new_wt is False
    assert run.wt == ticket.resolve(), "the existing worktree must be reused, not recreated"
    assert fake_agent.pending() == 0


def test_t7_dismiss_acks_deletes_and_removes_the_row(
    redis_client, harness, git_floor, mc_wire
) -> None:
    """Catches stream cleanup drifting apart from GUI teardown."""
    ticket = git_floor.add_ticket("t7-ticket")
    git_floor.commit(ticket, "feature.txt", "a feature\n")
    stream = mc_wire.finished_stream(REPO)

    manager, key, _entry_id = merge_row(
        harness, redis_client, mc_wire, git_floor, ticket_name="t7-ticket", wt=ticket
    )
    manager.click(f"merge::{key}")
    harness.pump(2)
    assert manager.shown(f"dismiss::{key}")

    manager.click(f"dismiss::{key}")
    harness.pump(2)

    assert redis_client.xlen(stream) == 0, "dismiss must XDEL the FINISHED entry"
    assert _pending(redis_client, stream) == 0, "dismiss must XACK the FINISHED entry"
    assert not manager.exists(f"name::{key}"), "the row widget outlived its entry"
    assert not manager.exists(f"merge::{key}")


# --- T8: the whole chain over one shared frame pump ------------------------


def test_t8_full_chain_from_dispatch_to_merged_agents(
    redis_client, fake_gh, harness, git_floor, fake_agent, mc_wire
) -> None:
    """Dispatch → FakeAgent → MergeManager row → merge, with both FEs hosted.

    The only scenario where two FEs share pump state, which is the exact
    condition under which the frame-pump blockers manifest.
    """
    fake_gh.add_issue(88, "t8-ticket", "Do the thing.")

    dispatcher = connect_dispatcher(harness)
    manager = harness.drop("merge_manager")

    dispatch(harness, dispatcher, 88)
    assert redis_client.xlen(WORKORDER_STREAM) == 1

    runs = fake_agent.run_once()
    assert len(runs) == 1
    run = runs[0]

    key = f"{REPO}|{run.finished_id}"
    harness.wait_for_widget(manager, f"name::{key}")

    # TicketDispatcher must still be draining while MergeManager works: both
    # depend on the same shared pump, and its status is written by its own
    # background thread.
    harness.wait_until(
        lambda: "agent-ready" in dispatcher.get("status_text"),
        message="TicketDispatcher to keep draining alongside MergeManager",
    )
    assert dispatcher.exists("ticket_btn_88")

    manager.click(f"merge::{key}")
    harness.pump(2)

    assert git_floor.contains(git_floor.agents_dir, run.commit_sha), (
        "the agent's commit never reached agents: "
        f"{git_floor.subjects(git_floor.agents_dir)}"
    )
    assert git_floor.origin_sha("agents") == git_floor.head(git_floor.agents_dir)
    assert manager.shown(f"dismiss::{key}")
    harness.screenshot("t8-merged")
