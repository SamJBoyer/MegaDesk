"""VoiceDeck pane: docked under the canvas row, collapsible, not a Catalog node."""

from __future__ import annotations

from pathlib import Path

import dearpygui.dearpygui as dpg
import pytest
from megadesk_contracts.testing import CanvasHarness, invoke_callback

PANEL = "voice_deck_panel_window"
BODY = "voice_deck_panel_window::body"
TOGGLE = "voice_deck_panel_window::toggle"
TALK = "voice_deck_panel_window::talk_btn"


@pytest.fixture
def panel_harness(tmp_path: Path, artifacts_dir: Path, fast_polling: None):
    from megadesk_contracts import frame_pump

    frame_pump.reset()
    canvas = CanvasHarness(
        graph_path=tmp_path / "graph.json",
        artifacts_dir=artifacts_dir,
        supervisor_panel=True,
        voice_deck_panel=True,
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


@pytest.mark.canvas
def test_voice_deck_is_docked_under_the_canvas_row(panel_harness) -> None:
    from engine.display_engine import CANVAS_BODY_TAG, GRAPH_WINDOW

    assert dpg.does_item_exist(PANEL)
    assert dpg.does_item_exist(TALK)
    assert _alias(dpg.get_item_parent(PANEL)) == GRAPH_WINDOW
    assert _alias(dpg.get_item_parent(CANVAS_BODY_TAG)) == GRAPH_WINDOW


@pytest.mark.canvas
def test_voice_deck_is_not_in_the_catalog(panel_harness) -> None:
    from engine.megadesk_registry import all_fe_specs

    names = {spec.name for spec in all_fe_specs()}
    assert "voice_deck" not in names
    with pytest.raises(KeyError, match="voice_deck"):
        panel_harness.drop("voice_deck")


@pytest.mark.canvas
def test_voice_deck_discovers_node_tools(panel_harness) -> None:
    from engine.megadesk_registry import all_fe_specs, all_tool_specs

    names = {spec.name for spec in all_tool_specs()}
    assert {"code_scope", "work_dispatcher", "voice_deck", "canvas"} <= names
    assert "canvas" not in {spec.name for spec in all_fe_specs()}
    tools = {schema["name"] for spec in all_tool_specs() for schema in spec.schemas}
    assert "ask_codebase" in tools
    assert "list_tickets" in tools
    assert "end_session" in tools
    assert "list_nodes" in tools
    assert "type_into" in tools


@pytest.mark.canvas
def test_voice_deck_collapse(panel_harness) -> None:
    from engine.display_engine import COLLAPSED_PANEL_WIDTH, VOICE_DECK_HEIGHT

    assert panel_harness.engine.voice_deck_expanded is True
    assert int(dpg.get_item_configuration(PANEL).get("height") or 0) == VOICE_DECK_HEIGHT
    assert dpg.get_item_configuration(BODY).get("show", True)

    _click(TOGGLE)
    panel_harness.pump(1)
    assert panel_harness.engine.voice_deck_expanded is False
    assert (
        int(dpg.get_item_configuration(PANEL).get("height") or 0) == COLLAPSED_PANEL_WIDTH
    )
    assert dpg.get_item_configuration(BODY).get("show") is False

    _click(TOGGLE)
    panel_harness.pump(1)
    assert panel_harness.engine.voice_deck_expanded is True
    assert int(dpg.get_item_configuration(PANEL).get("height") or 0) == VOICE_DECK_HEIGHT
    assert dpg.get_item_configuration(BODY).get("show", True)


def test_ensure_voice_deck_is_a_singleton(monkeypatch) -> None:
    from voice_deck.panel import ensure_voice_deck_running

    launches: list[str] = []

    class _AlreadyRunning:
        redis_url = "redis://localhost:6379/14"

        def redis_ok(self) -> bool:
            return True

        def backend_ok(self) -> bool:
            return True

        def list_running(self) -> list[dict[str, str]]:
            return [{"node_endpoint": "voice_deck"}]

        def launch_node(self, name: str, parameters: object = "") -> str:
            launches.append(name)
            return "1-0"

    monkeypatch.setattr("voice_deck.panel.SupervisorClient", _AlreadyRunning)
    assert ensure_voice_deck_running() is True
    assert launches == []


def test_ensure_voice_deck_launches_when_absent(monkeypatch) -> None:
    from voice_deck.panel import ensure_voice_deck_running

    launches: list[str] = []

    class _Idle:
        redis_url = "redis://localhost:6379/14"

        def redis_ok(self) -> bool:
            return True

        def backend_ok(self) -> bool:
            return True

        def list_running(self) -> list[dict[str, str]]:
            return []

        def launch_node(self, name: str, parameters: object = "") -> str:
            launches.append(name)
            return "1-0"

    monkeypatch.setattr("voice_deck.panel.SupervisorClient", _Idle)
    assert ensure_voice_deck_running() is True
    assert launches == ["voice_deck"]
