"""Standalone Dear PyGui prototypes (not BaseNode canvas tools).

Run any module directly:
  python -m new_nodes.quick_note
  python -m new_nodes.feature_brief
  python -m new_nodes.agent_board

Or place them on the alt canvas:
  python alt_canvas_test.py
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from new_nodes import agent_board, feature_brief, quick_note


@dataclass(frozen=True)
class GuiSpec:
    key: str
    label: str
    width: int
    height: int
    build: Callable[..., str]


GUI_CATALOG: tuple[GuiSpec, ...] = (
    GuiSpec(
        key="quick_note",
        label=quick_note.LABEL,
        width=quick_note.WIN_W,
        height=quick_note.WIN_H,
        build=quick_note.build_ui,
    ),
    GuiSpec(
        key="feature_brief",
        label=feature_brief.LABEL,
        width=feature_brief.WIN_W,
        height=feature_brief.WIN_H,
        build=feature_brief.build_ui,
    ),
    GuiSpec(
        key="agent_board",
        label=agent_board.LABEL,
        width=agent_board.WIN_W,
        height=agent_board.WIN_H,
        build=agent_board.build_ui,
    ),
)


def get_gui(key: str) -> Optional[GuiSpec]:
    key_l = key.strip().lower()
    for spec in GUI_CATALOG:
        if spec.key == key_l or spec.label.lower() == key_l:
            return spec
    return None
