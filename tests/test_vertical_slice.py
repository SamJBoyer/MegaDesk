"""Vertical slice: TicketDispatcher → MissionControl (Plant) → MergeManager.

Uses the public smoke-test repo URL and one agent-ready issue. The sandbox
boundary is still FakeAgent (no Docker, no Cursor API), but MissionControl's
FE is hosted and must show a live AGENTHANDLER while the agent works — the
gap T8 left open.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import WORKORDER_STREAM

pytestmark = [pytest.mark.canvas, pytest.mark.redis, pytest.mark.git]

SMOKE_URL = "https://github.com/SamJBoyer/SMOKETESTREPO.git"
SMOKE_REPO = "SMOKETESTREPO"
ISSUE_NUMBER = 1
ISSUE_TITLE = "smoketest-counter"
ISSUE_BODY = "Increment the counter in counter.txt by one."


@pytest.fixture
def smoke_floor(tmp_path: Path):
    from megadesk_contracts.testing import GitFloor

    floor = GitFloor(tmp_path / "floor-root", repo=SMOKE_REPO)
    floor.create()
    try:
        yield floor
    finally:
        floor.destroy()


@pytest.fixture
def smoke_agent(redis_client, smoke_floor, mc_wire):
    from megadesk_contracts.testing import FakeAgent

    agent = FakeAgent(
        redis=redis_client,
        floor=smoke_floor,
        wire=mc_wire,
        group="mission_control",
    )
    agent.ensure_group()
    return agent


def test_ticket_plant_merge_vertical_slice(
    redis_client,
    fake_gh,
    harness,
    smoke_floor,
    smoke_agent,
    mc_wire,
) -> None:
    """Dispatch the smoke-test issue, show a live Plant sandbox, then merge."""
    fake_gh.add_issue(ISSUE_NUMBER, ISSUE_TITLE, ISSUE_BODY)

    dispatcher = harness.drop("ticket_dispatcher")
    plant = harness.drop("mission_control")
    manager = harness.drop("merge_manager")

    dispatcher.type_into("git_url", SMOKE_URL)
    harness.wait_for_widget(dispatcher, f"ticket_btn_{ISSUE_NUMBER}")
    dispatcher.click(f"ticket_btn_{ISSUE_NUMBER}")

    assert redis_client.xlen(WORKORDER_STREAM) == 1
    workorder_id = redis_client.xrange(WORKORDER_STREAM)[0][0]
    fields = redis_client.xrange(WORKORDER_STREAM)[0][1]
    assert fields["repo"] == SMOKE_REPO
    assert fields["URL"] == "https://github.com/SamJBoyer/SMOKETESTREPO"
    assert fields["new_wt"] == "true"
    assert fields["ticket_name"] == ISSUE_TITLE

    guid = "slice-live-agent"
    redis_client.hset(
        f"AGENTHANDLER:{guid}",
        mapping=mc_wire.agent_handler_fields(
            ticket_id=str(workorder_id),
            status="running",
        ),
    )
    harness.wait_until(
        lambda: "live=1" in plant.get("status_lbl"),
        message="MissionControl to show the live AgentHandler sandbox",
    )
    live_items = plant.items("live_list")
    assert any("running" in item for item in live_items)

    runs = smoke_agent.run_once()
    assert len(runs) == 1
    run = runs[0]
    assert run.repo == SMOKE_REPO
    assert run.ticket_name == ISSUE_TITLE
    redis_client.delete(f"AGENTHANDLER:{guid}")

    key = f"{SMOKE_REPO}|{run.finished_id}"
    harness.wait_for_widget(manager, f"name::{key}")
    manager.click(f"merge::{key}")
    harness.pump(2)

    assert smoke_floor.contains(smoke_floor.agents_dir, run.commit_sha)
    assert smoke_floor.origin_sha("agents") == smoke_floor.head(smoke_floor.agents_dir)
    assert manager.shown(f"dismiss::{key}")
    harness.screenshot("vertical-slice-merged")
