"""Smoke tests for piloting the real canvas.

These assert the harness itself reaches production code: the canvas chrome the
engine builds, the Catalog drop path, tag-addressable FE widgets, the close
button, and a readable screenshot. If any of these break, every scenario in
`test_nodeflow.py` becomes untrustworthy rather than merely failing.
"""

from __future__ import annotations

import json

import dearpygui.dearpygui as dpg
import pytest
from megadesk_contracts.testing import HarnessTimeout, WidgetMissing

pytestmark = pytest.mark.canvas


def test_boot_builds_the_engine_chrome(harness) -> None:
    assert harness.model.members == {}
    from engine.display_engine import (
        CATALOG_TOGGLE_TAG,
        GRAPH_WINDOW,
        NODE_EDITOR,
        SIDEBAR_TAG,
    )
    from engine.graph_bar import GRAPH_BAR_TAG, SELECT_TAG, CAPTURE_TAG

    assert dpg.does_item_exist(GRAPH_WINDOW)
    assert dpg.does_item_exist(SIDEBAR_TAG)
    assert dpg.does_item_exist(CATALOG_TOGGLE_TAG)
    assert dpg.does_item_exist(NODE_EDITOR)
    assert dpg.does_item_exist(GRAPH_BAR_TAG)
    assert dpg.does_item_exist(SELECT_TAG)
    assert dpg.does_item_exist(CAPTURE_TAG)


def test_catalog_offers_the_installed_frontends(harness) -> None:
    from engine.megadesk_registry import all_fe_specs

    names = {spec.name for spec in all_fe_specs()}
    assert {"ticket_dispatcher", "pr_manager"} <= names


def test_drop_hosts_the_fe_and_persists_the_member(harness, tmp_path) -> None:
    driver = harness.drop("ticket_dispatcher")

    assert driver.member_id in harness.model.members
    assert driver.is_hosted()
    # Tags the FE derives from the tag_prefix the host handed to FeSpec.build.
    for suffix in ("git_url", "status_text", "ticket_scroll"):
        assert driver.exists(suffix), f"missing {suffix}: {driver.suffixes()}"

    saved = json.loads((tmp_path / "graph.json").read_text(encoding="utf-8"))
    member = saved["members"][driver.member_id]
    assert member["type"] == "megadesk"
    assert member["node_name"] == "ticket_dispatcher"
    assert "parameters" in member
    assert "scale" not in member
    assert "parents" not in member
    assert "children" not in member
    assert "canvas_id" not in member


def test_widgets_are_writable_and_callbacks_are_real(harness) -> None:
    driver = harness.drop("ticket_dispatcher")

    driver.type_into("git_url", "https://github.com/acme/widgets")
    assert driver.get("git_url") == "https://github.com/acme/widgets"
    # The input's own callback queues a status change, drained by the frame pump.
    harness.wait_until(
        lambda: driver.get("status_text") != "Idle",
        message="TicketDispatcher status to leave Idle",
    )


def test_addressing_a_missing_widget_fails_loudly(harness) -> None:
    driver = harness.drop("ticket_dispatcher")
    with pytest.raises(WidgetMissing):
        driver.get("no_such_widget")


def test_close_button_removes_the_member_from_the_model(harness) -> None:
    driver = harness.drop("ticket_dispatcher")
    assert harness.model.members

    driver.close()

    assert driver.member_id not in harness.model.members
    assert not driver.is_hosted()


def test_screenshot_writes_a_real_render(harness) -> None:
    harness.drop("ticket_dispatcher")
    path = harness.screenshot("smoke")

    assert path.is_file()
    # A minimized viewport renders nothing and yields a ~79 byte empty PNG.
    assert path.stat().st_size > 10_000, f"{path} is {path.stat().st_size} bytes"


def test_wait_until_raises_and_leaves_an_artifact(harness) -> None:
    harness.drop("ticket_dispatcher")
    with pytest.raises(HarnessTimeout) as excinfo:
        harness.wait_until(lambda: False, timeout=0.2, message="the impossible")

    assert "the impossible" in str(excinfo.value)
    assert "screenshot" in str(excinfo.value)


def test_first_node_on_an_empty_board_still_updates(harness) -> None:
    """The live bug behind blocker 3.1, at canvas level.

    The committed board has three members, so the pump gets armed at frame 0
    during startup and this stays hidden. Here the board starts empty and the
    Supervisor panel is off, so the dropped node is the very first thing to
    register — after several frames have already rendered.
    """
    assert harness.model.members == {}
    assert dpg.get_frame_count() > 1

    driver = harness.drop("ticket_dispatcher")

    # Only reachable if the FE's per-frame drain is actually running: the text
    # is written by the poll thread through _ui_queue.
    harness.wait_until(
        lambda: driver.get("status_text") == "Enter a GitHub repository URL",
        message="the first node's queue to drain through the shared frame pump",
    )


def test_two_frontends_share_one_live_pump(harness, fake_gh) -> None:
    dispatcher = harness.drop("ticket_dispatcher")
    manager = harness.drop("pr_manager")

    probe = harness.install_pump_probe()
    harness.pump(10)
    assert probe.ticks >= 8

    harness.wait_until(
        lambda: dispatcher.get("status_text") == "Enter a GitHub repository URL",
        message="TicketDispatcher to drain with a second FE on the board",
    )
    assert manager.exists("issue_scroll")
