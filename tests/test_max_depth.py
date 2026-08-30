"""PRManager and AutoIntegrate size their lists from MAX_DEPTH, like WorkDispatcher."""

from __future__ import annotations

import dearpygui.dearpygui as dpg
import pytest
from auto_integrate_app import ROW_H as AI_ROW_H
from pr_manager_app import ROW_H as PM_ROW_H

pytestmark = pytest.mark.canvas


@pytest.mark.parametrize(
    ("node", "row_h"),
    (
        ("pr_manager", PM_ROW_H),
        ("auto_integrate", AI_ROW_H),
    ),
)
def test_depth_extrudes_the_list_to_fit_rows(harness, node: str, row_h: int) -> None:
    gate = harness.drop(node)
    start = dpg.get_item_configuration(gate.require("issue_scroll"))["height"]
    assert start == 2 * row_h

    gate.set("max_depth", 5)
    gate.fire("max_depth", 5)

    assert dpg.get_item_configuration(gate.require("issue_scroll"))["height"] == 5 * row_h
    content_h = dpg.get_item_configuration(f"{gate.tag_prefix}::content")["height"]
    assert content_h >= 5 * row_h
