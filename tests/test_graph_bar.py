"""Graph bar seams: Capture, GIT_URL from a saved graph, refuse a random JSON."""

from __future__ import annotations

import json
from pathlib import Path

import dearpygui.dearpygui as dpg
import pytest
from megadesk_contracts.testing import CanvasHarness, invoke_callback

pytestmark = pytest.mark.canvas

GIT_URL = "https://github.com/acme/widgets"


def _ticket_graph(member_id: str = "td-1") -> dict:
    return {
        "members": {
            member_id: {
                "member_id": member_id,
                "type": "megadesk",
                "nickname": "ticket_dispatcher",
                "node_name": "ticket_dispatcher",
                "position": [40.0, 40.0],
                "parameters": {"GIT_URL": GIT_URL},
                "data": {
                    "width": 480.0,
                    "height": 160.0,
                    "node_name": "ticket_dispatcher",
                },
            }
        }
    }


def test_ticket_dispatcher_boots_from_graph_git_url(
    tmp_path: Path, artifacts_dir: Path, fast_polling: None, fake_gh
) -> None:
    path = tmp_path / "graph.json"
    path.write_text(json.dumps(_ticket_graph()), encoding="utf-8")
    with CanvasHarness(graph_path=path, artifacts_dir=artifacts_dir, supervisor_panel=False) as harness:
        driver = harness.driver_for("ticket_dispatcher")
        assert driver.get("git_url") == GIT_URL


def test_capture_presses_live_values_into_the_graph(harness, tmp_path: Path) -> None:
    from engine.graph_bar import CAPTURE_TAG, STATUS_TAG

    driver = harness.drop("ticket_dispatcher")
    driver.type_into("git_url", GIT_URL)

    callback = dpg.get_item_callback(CAPTURE_TAG)
    assert callback is not None
    invoke_callback(callback, CAPTURE_TAG, None, None)

    saved = json.loads((tmp_path / "graph.json").read_text(encoding="utf-8"))
    assert saved["members"][driver.member_id]["parameters"]["GIT_URL"] == GIT_URL
    assert "captured" in dpg.get_value(STATUS_TAG)


def test_a_random_json_is_refused_and_the_board_stays(harness, tmp_path: Path) -> None:
    from engine.graph_bar import STATUS_TAG

    driver = harness.drop("ticket_dispatcher")
    member_id = driver.member_id
    junk = tmp_path / "package.json"
    junk.write_text('{"name": "not-a-graph"}', encoding="utf-8")

    harness.engine.graph_bar._load(junk)

    assert member_id in harness.model.members
    assert driver.is_hosted()
    assert "not a graph" in dpg.get_value(STATUS_TAG).lower()


def test_loading_another_graph_replaces_the_board(
    harness, tmp_path: Path, fake_gh
) -> None:
    dropped = harness.drop("ticket_dispatcher")
    other = tmp_path / "other.json"
    other.write_text(
        json.dumps(
            {
                "members": {
                    "pm-1": {
                        "member_id": "pm-1",
                        "type": "megadesk",
                        "node_name": "pr_manager",
                        "position": [40.0, 40.0],
                        "parameters": {"GIT_URL": GIT_URL},
                        "data": {
                            "width": 480.0,
                            "height": 160.0,
                            "node_name": "pr_manager",
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    harness.load_graph(other)

    assert dropped.member_id not in harness.model.members
    assert not dropped.is_hosted()
    manager = harness.driver_for("pr_manager")
    assert manager.exists("issue_scroll")
    assert manager.get("git_url") == GIT_URL
