"""Supervisor pane: docked right, collapsible, Nodes/Logs, canvas click → log."""

from __future__ import annotations

from pathlib import Path

import dearpygui.dearpygui as dpg
import pytest
from megadesk_contracts.log_session import begin_log_session, session_log_path
from megadesk_contracts.testing import CanvasHarness, invoke_callback

pytestmark = pytest.mark.canvas

PROCESS_LOG = "supervisor_panel_window::process_log"
TABS = "supervisor_panel_window::tabs"
TAB_NODES = "supervisor_panel_window::tab_nodes"
TAB_LOGS = "supervisor_panel_window::tab_logs"


@pytest.fixture
def panel_harness(tmp_path: Path, artifacts_dir: Path, fast_polling: None):
    from megadesk_contracts import frame_pump

    frame_pump.reset()
    canvas = CanvasHarness(
        graph_path=tmp_path / "graph.json",
        artifacts_dir=artifacts_dir,
        supervisor_panel=True,
    )
    canvas.boot()
    try:
        yield canvas
    finally:
        canvas.shutdown()
        frame_pump.reset()


def _alias(item) -> str:
    try:
        return str(dpg.get_item_alias(item) or item)
    except Exception:
        return str(item)


def _click(tag: str) -> None:
    callback = dpg.get_item_callback(tag)
    assert callback is not None, f"{tag} has no callback"
    invoke_callback(callback, tag, None, None)


def test_supervisor_is_docked_in_the_canvas_row(panel_harness) -> None:
    from engine.display_engine import (
        CANVAS_BODY_TAG,
        GRAPH_WINDOW,
        SIDEBAR_TAG,
        SUPERVISOR_PANEL_TAG,
    )

    assert dpg.does_item_exist(SUPERVISOR_PANEL_TAG)
    assert _alias(dpg.get_item_parent(SUPERVISOR_PANEL_TAG)) == CANVAS_BODY_TAG
    assert _alias(dpg.get_item_parent(SIDEBAR_TAG)) == CANVAS_BODY_TAG
    assert _alias(dpg.get_item_parent(CANVAS_BODY_TAG)) == GRAPH_WINDOW


def test_supervisor_has_nodes_and_logs_tabs(panel_harness) -> None:
    assert dpg.does_item_exist(TABS)
    assert dpg.does_item_exist(TAB_NODES)
    assert dpg.does_item_exist(TAB_LOGS)
    assert dpg.does_item_exist(PROCESS_LOG)
    assert _alias(dpg.get_item_parent(TAB_NODES)) == TABS
    assert _alias(dpg.get_item_parent(TAB_LOGS)) == TABS
    # Process log lives under Logs, not Nodes.
    parent = PROCESS_LOG
    seen: list[str] = []
    for _ in range(8):
        parent = _alias(dpg.get_item_parent(parent))
        seen.append(parent)
        if parent == TAB_LOGS:
            break
    assert TAB_LOGS in seen, seen
    assert TAB_NODES not in seen


def test_catalog_and_supervisor_collapse(panel_harness) -> None:
    from engine.display_engine import (
        CATALOG_BODY_TAG,
        CATALOG_TOGGLE_TAG,
        CATALOG_WIDTH,
        COLLAPSED_PANEL_WIDTH,
        SIDEBAR_TAG,
        SUPERVISOR_BODY_TAG,
        SUPERVISOR_PANEL_TAG,
        SUPERVISOR_TOGGLE_TAG,
        SUPERVISOR_WIDTH,
    )

    assert int(dpg.get_item_configuration(SIDEBAR_TAG).get("width") or 0) == CATALOG_WIDTH
    assert int(dpg.get_item_configuration(SUPERVISOR_PANEL_TAG).get("width") or 0) == SUPERVISOR_WIDTH
    assert dpg.get_item_configuration(CATALOG_BODY_TAG).get("show", True)
    assert dpg.get_item_configuration(SUPERVISOR_BODY_TAG).get("show", True)

    _click(CATALOG_TOGGLE_TAG)
    panel_harness.pump(1)
    assert panel_harness.engine.catalog_expanded is False
    assert int(dpg.get_item_configuration(SIDEBAR_TAG).get("width") or 0) == COLLAPSED_PANEL_WIDTH
    assert dpg.get_item_configuration(CATALOG_BODY_TAG).get("show") is False

    _click(SUPERVISOR_TOGGLE_TAG)
    panel_harness.pump(1)
    assert panel_harness.engine.supervisor_expanded is False
    assert int(dpg.get_item_configuration(SUPERVISOR_PANEL_TAG).get("width") or 0) == COLLAPSED_PANEL_WIDTH
    assert dpg.get_item_configuration(SUPERVISOR_BODY_TAG).get("show") is False

    _click(CATALOG_TOGGLE_TAG)
    _click(SUPERVISOR_TOGGLE_TAG)
    panel_harness.pump(1)
    assert panel_harness.engine.catalog_expanded is True
    assert panel_harness.engine.supervisor_expanded is True
    assert int(dpg.get_item_configuration(SIDEBAR_TAG).get("width") or 0) == CATALOG_WIDTH
    assert int(dpg.get_item_configuration(SUPERVISOR_PANEL_TAG).get("width") or 0) == SUPERVISOR_WIDTH


def test_selecting_a_canvas_node_shows_its_log(panel_harness, monkeypatch) -> None:
    begin_log_session()
    marker = "canvas-click-log-marker"
    session_log_path("ticket_dispatcher").write_text(
        f"{marker}\n", encoding="utf-8"
    )

    driver = panel_harness.drop("ticket_dispatcher")
    hosted = f"megadesk::{driver.member_id}"
    monkeypatch.setattr(dpg, "get_selected_nodes", lambda *_a, **_k: [hosted])
    panel_harness.pump(2)

    assert marker in str(dpg.get_value(PROCESS_LOG) or "")
    assert _alias(dpg.get_value(TABS)) == TAB_LOGS


def test_notify_member_clicked_uses_the_same_log_path(panel_harness) -> None:
    begin_log_session()
    marker = "notify-click-log-marker"
    session_log_path("ticket_dispatcher").write_text(
        f"{marker}\n", encoding="utf-8"
    )

    driver = panel_harness.drop("ticket_dispatcher")
    panel_harness.engine.notify_member_clicked(driver.member_id)

    assert marker in str(dpg.get_value(PROCESS_LOG) or "")
    assert _alias(dpg.get_value(TABS)) == TAB_LOGS

    panel_harness.pump(1)
    assert marker in str(dpg.get_value(PROCESS_LOG) or "")
    assert _alias(dpg.get_value(TABS)) == TAB_LOGS
