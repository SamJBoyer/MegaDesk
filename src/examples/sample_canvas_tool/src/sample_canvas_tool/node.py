"""Example pip-discovered BaseNode for the MegaDesk canvas."""

from __future__ import annotations

from typing import Any, Optional

import dearpygui.dearpygui as dpg

from executive import BaseNode


class SampleToolNode(BaseNode):
    """Minimal external tool: canvas placard + optional floating editor."""

    nickname = "Sample Tool"
    global_guid = "sample_tool"
    icon = ""
    description = "Example pip plugin. Double-click to open a small editor."

    default_width = 200.0
    default_height = 120.0

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.data.setdefault("label", "Sample Tool")
        self.data.setdefault("fill", [230, 240, 250, 230])
        self.data.setdefault("edge", [40, 90, 150, 255])
        self._ui_window: Optional[str] = None

    def get_label(self) -> str:
        return str(self.data.get("label", self.nickname))

    def set_label(self, value: str) -> None:
        self.data["label"] = value

    def draw(
        self,
        drawlist: str | int,
        world_to_screen,
        selected: bool = False,
    ) -> None:
        x, y, w, h = self.bounds()
        pmin = world_to_screen(x, y)
        pmax = world_to_screen(x + w, y + h)
        fill = tuple(self.data.get("fill", [230, 240, 250, 230]))
        edge = tuple(self.data.get("edge", [40, 90, 150, 255]))
        thickness = 3 if selected else 1.5

        a = world_to_screen(0, 0)
        b = world_to_screen(1, 0)
        zoom = max(0.2, abs(b[0] - a[0]))

        dpg.draw_rectangle(
            pmin,
            pmax,
            color=edge,
            fill=fill,
            thickness=thickness,
            rounding=4,
            parent=drawlist,
        )
        title_pos = world_to_screen(x + 8, y + 8)
        dpg.draw_text(
            title_pos,
            self.get_label(),
            size=max(10, 14 * zoom),
            color=(25, 30, 40, 255),
            parent=drawlist,
        )
        hint_pos = world_to_screen(x + 8, y + 32)
        dpg.draw_text(
            hint_pos,
            "Double-click to edit",
            size=max(9, 11 * zoom),
            color=(80, 90, 110, 255),
            parent=drawlist,
        )

    def on_double_click(self) -> None:
        tag = f"sample_tool_ui::{self.canvas_id}"
        if dpg.does_item_exist(tag):
            dpg.focus_item(tag)
            self._ui_window = tag
            return

        def _save() -> None:
            if dpg.does_item_exist(f"{tag}::input"):
                self.set_label(dpg.get_value(f"{tag}::input"))

        with dpg.window(
            label=self.nickname,
            tag=tag,
            width=320,
            height=160,
            on_close=lambda: setattr(self, "_ui_window", None),
        ):
            dpg.add_input_text(
                tag=f"{tag}::input",
                default_value=self.get_label(),
                width=-1,
            )
            dpg.add_button(label="Save", callback=lambda: _save())
        self._ui_window = tag

    def on_destroy(self) -> None:
        if self._ui_window and dpg.does_item_exist(self._ui_window):
            dpg.delete_item(self._ui_window)
        self._ui_window = None
