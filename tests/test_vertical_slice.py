"""Vertical slice: WorkDispatcher → MachineFactory → PRManager.

Uses the public smoke-test repo URL and one agent-ready issue. The sandbox
boundary is still FakeAgent (no Docker, no Cursor API), but MachineFactory's
FE is hosted and must show a live AGENTHANDLER while the agent works — the
gap T8 left open. PRManager lists a merge_success issue on the same repo URL.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import WORKORDER_GROUP, WORKORDER_STREAM

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
def smoke_agent(redis_client, machine_wire):
    from megadesk_contracts.testing import FakeAgent

    agent = FakeAgent(
        redis=redis_client,
        wire=machine_wire,
        group=WORKORDER_GROUP,
    )
    agent.ensure_group()
    return agent


def test_ticket_factory_merge_vertical_slice(
    redis_client,
    fake_gh,
    harness,
    smoke_floor,
    smoke_agent,
    machine_wire,
) -> None:
    """Dispatch the smoke-test issue, show a live factory sandbox, then a PR row."""
    fake_gh.add_issue(ISSUE_NUMBER, ISSUE_TITLE, ISSUE_BODY)
    fake_gh.add_merge_success(
        2,
        "merge_success: PR #1 can merge into dev",
        "https://github.com/SamJBoyer/SMOKETESTREPO/pull/1",
    )

    dispatcher = harness.drop("work_dispatcher")
    factory = harness.drop("machine_factory")
    manager = harness.drop("pr_manager")

    dispatcher.type_into("git_url", SMOKE_URL)
    manager.type_into("git_url", SMOKE_URL)
    harness.wait_for_widget(dispatcher, f"ticket_btn_{ISSUE_NUMBER}")
    dispatcher.click(f"ticket_btn_{ISSUE_NUMBER}")

    assert redis_client.xlen(WORKORDER_STREAM) == 1
    workorder_id = redis_client.xrange(WORKORDER_STREAM)[0][0]
    fields = redis_client.xrange(WORKORDER_STREAM)[0][1]
    assert fields["repo"] == SMOKE_REPO
    assert fields["URL"] == "https://github.com/SamJBoyer/SMOKETESTREPO"
    assert fields["auto_pr"] == "true"
    assert fields["ticket_name"] == ISSUE_TITLE

    guid = "slice-live-agent"
    redis_client.hset(
        f"AGENTHANDLER:{guid}",
        mapping=machine_wire.agent_handler_fields(
            ticket_id=str(workorder_id),
            status="running",
        ),
    )
    harness.wait_until(
        lambda: any("running" in item for item in factory.items("live_list")),
        message="MachineFactory to show the live AgentHandler sandbox",
    )
    live_items = factory.items("live_list")
    assert any("running" in item for item in live_items)

    runs = smoke_agent.run_once()
    assert len(runs) == 1
    run = runs[0]
    assert run.repo == SMOKE_REPO
    assert run.ticket_name == ISSUE_TITLE
    redis_client.delete(f"AGENTHANDLER:{guid}")

    harness.wait_for_widget(manager, "name::2")
    assert manager.enabled("open_pr::2")
    assert manager.shown("dismiss::2")
    assert run.pr_url
    harness.screenshot("vertical-slice-merge-success-pr")
