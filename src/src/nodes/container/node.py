"""Container test node — transparent frame that parents contained objects."""

from __future__ import annotations

import dearpygui.dearpygui as dpg

from engine.base_node import BaseNode
from engine.registry import register


@register
class ContainerNode(BaseNode):
    nickname = "Container"
    global_guid = "container"
    icon = ""
    description = "Transparent frame. Objects inside become children."

    has_parent_limit = False
    parent_limit = 0
    has_child_limit = False
    child_limit = 0

    is_container = True

    default_width = 320.0
    default_height = 240.0

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.data.setdefault("edge", [0, 0, 0, 255])

    def draw(self, drawlist, world_to_screen, selected: bool = False) -> None:
        x, y, w, h = self.bounds()
        pmin = world_to_screen(x, y)
        pmax = world_to_screen(x + w, y + h)
        edge = tuple(self.data.get("edge", [0, 0, 0, 255]))
        thickness = 3 if selected else 2

        # Light daytime tint so the frame is findable on a white board
        dpg.draw_rectangle(
            pmin,
            pmax,
            color=edge,
            fill=(40, 80, 140, 16),
            thickness=thickness,
            parent=drawlist,
        )
        label_pos = world_to_screen(x + 6, y + 4)
        a = world_to_screen(0, 0)
        b = world_to_screen(1, 0)
        zoom = max(0.2, abs(b[0] - a[0]))
        dpg.draw_text(
            label_pos,
            f"{self.nickname} ({len(self.children)})",
            size=max(10, 13 * zoom),
            color=(0, 0, 0, 220),
            parent=drawlist,
        )
        # Selection resize handles are drawn by DisplayEngine via BaseNode.
