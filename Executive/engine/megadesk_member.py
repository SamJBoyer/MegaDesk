"""Thin canvas placard for MegaDesk.nodes FE tools (not BaseNode)."""

from __future__ import annotations

from typing import Any, Optional
from uuid import uuid4

import dearpygui.dearpygui as dpg

from megadesk import FeSpec

from engine.base_node import HANDLE_HALF, MIN_SCALE

TYPE_DISCRIMINATOR = "megadesk"


class MegaDeskMember:
    """Lightweight canvas member backed by an FeSpec build() callable.

    When the FE window is open it is world-anchored: position is in canvas
    world coords, while width/height stay in screen pixels (windows do not
    scale with zoom — same model as ``alt_canvas_test``).
    """

    is_container: bool = False

    def __init__(
        self,
        spec: FeSpec,
        *,
        canvas_id: Optional[str] = None,
        position: Optional[tuple[float, float]] = None,
        scale: Any = 1.0,
        parents: Optional[list[str]] = None,
        children: Optional[list[str]] = None,
        data: Optional[dict[str, Any]] = None,
    ) -> None:
        self.spec = spec
        self.name = spec.name
        self.nickname = spec.name
        self.global_guid = TYPE_DISCRIMINATOR
        self.description = spec.description
        self.canvas_id: str = canvas_id or str(uuid4())
        self.position: list[float] = list(position or (0.0, 0.0))
        if isinstance(scale, (list, tuple)) and len(scale) >= 2:
            self.scale_x = float(scale[0])
            self.scale_y = float(scale[1])
        else:
            try:
                s = float(scale)
            except (TypeError, ValueError):
                s = 1.0
            self.scale_x = s
            self.scale_y = s
        self.parents: list[str] = list(parents or [])
        self.children: list[str] = list(children or [])
        self.data: dict[str, Any] = dict(data or {})
        self.width: float = float(self.data.get("width", spec.default_width))
        self.height: float = float(self.data.get("height", spec.default_height))
        self._selected = False
        self._window_tag: Optional[str] = None

    @property
    def window_tag(self) -> Optional[str]:
        return self._window_tag

    def is_gui_open(self) -> bool:
        return bool(self._window_tag and dpg.does_item_exist(self._window_tag))

    def to_member_dict(self) -> dict[str, Any]:
        self.data["width"] = self.width
        self.data["height"] = self.height
        self.data["node_name"] = self.name
        return {
            "canvas_id": self.canvas_id,
            "type": TYPE_DISCRIMINATOR,
            "nickname": self.nickname,
            "node_name": self.name,
            "position": [float(self.position[0]), float(self.position[1])],
            "scale": [float(self.scale_x), float(self.scale_y)],
            "parents": list(self.parents),
            "children": list(self.children),
            "data": dict(self.data),
        }

    @classmethod
    def from_member_dict(cls, member: dict[str, Any], spec: FeSpec) -> "MegaDeskMember":
        data = dict(member.get("data") or {})
        return cls(
            spec,
            canvas_id=member.get("canvas_id"),
            position=tuple(member.get("position", (0.0, 0.0))),
            scale=member.get("scale", 1.0),
            parents=member.get("parents"),
            children=member.get("children"),
            data=data,
        )

    def bounds(self) -> tuple[float, float, float, float]:
        w = self.width * self.scale_x
        h = self.height * self.scale_y
        return self.position[0], self.position[1], w, h

    def contains_point(self, x: float, y: float) -> bool:
        # Open GUIs receive input via their DPG window; placard hit is for closed state.
        if self.is_gui_open():
            return False
        bx, by, bw, bh = self.bounds()
        return bx <= x <= bx + bw and by <= y <= by + bh

    def move_by(self, dx: float, dy: float) -> None:
        self.position[0] += dx
        self.position[1] += dy

    def handle_centers(self) -> dict[str, tuple[float, float]]:
        x, y, w, h = self.bounds()
        return {
            "nw": (x, y),
            "n": (x + w * 0.5, y),
            "ne": (x + w, y),
            "e": (x + w, y + h * 0.5),
            "se": (x + w, y + h),
            "s": (x + w * 0.5, y + h),
            "sw": (x, y + h),
            "w": (x, y + h * 0.5),
        }

    def hit_resize_handle(
        self, world_x: float, world_y: float, zoom: float = 1.0
    ) -> Optional[str]:
        if self.is_gui_open():
            return None
        half = HANDLE_HALF / max(zoom, 1e-6)
        for hid, (hx, hy) in self.handle_centers().items():
            if abs(world_x - hx) <= half and abs(world_y - hy) <= half:
                return hid
        return None

    def resize_to_point(self, handle: str, world_x: float, world_y: float) -> None:
        x, y, w, h = self.bounds()
        right = x + w
        bottom = y + h
        min_w = self.width * MIN_SCALE
        min_h = self.height * MIN_SCALE

        new_left, new_right = x, right
        new_top, new_bottom = y, bottom

        if handle in ("nw", "sw", "w"):
            new_left = min(world_x, right - min_w)
        if handle in ("ne", "se", "e"):
            new_right = max(world_x, x + min_w)
        if handle in ("nw", "ne", "n"):
            new_top = min(world_y, bottom - min_h)
        if handle in ("sw", "se", "s"):
            new_bottom = max(world_y, y + min_h)

        new_w = max(min_w, new_right - new_left)
        new_h = max(min_h, new_bottom - new_top)
        self.position[0] = new_left
        self.position[1] = new_top
        self.scale_x = new_w / max(self.width, 1e-6)
        self.scale_y = new_h / max(self.height, 1e-6)

    def draw_resize_handles(self, drawlist: str | int, world_to_screen) -> None:
        if self.is_gui_open():
            return
        x, y, w, h = self.bounds()
        pmin = world_to_screen(x, y)
        pmax = world_to_screen(x + w, y + h)
        dpg.draw_rectangle(
            (pmin[0] - 3, pmin[1] - 3),
            (pmax[0] + 3, pmax[1] + 3),
            color=(80, 160, 255, 220),
            fill=(0, 0, 0, 0),
            thickness=2,
            parent=drawlist,
        )
        half = HANDLE_HALF
        for hx, hy in self.handle_centers().values():
            sx, sy = world_to_screen(hx, hy)
            dpg.draw_rectangle(
                (sx - half, sy - half),
                (sx + half, sy + half),
                color=(40, 90, 180, 255),
                fill=(240, 248, 255, 255),
                thickness=1.5,
                parent=drawlist,
            )

    def draw(
        self,
        drawlist: str | int,
        world_to_screen,
        selected: bool = False,
    ) -> None:
        sx, sy = world_to_screen(self.position[0], self.position[1])
        if self.is_gui_open():
            # Screen-pixel footprint under the floating window (does not zoom).
            dpg.draw_rectangle(
                (sx - 2, sy - 2),
                (sx + self.width + 2, sy + self.height + 2),
                color=(180, 190, 210, 120),
                fill=(230, 235, 245, 40),
                thickness=1,
                parent=drawlist,
            )
            return

        x, y, w, h = self.bounds()
        p0 = world_to_screen(x, y)
        p1 = world_to_screen(x + w, y + h)
        fill = (230, 236, 245, 230) if not selected else (210, 225, 245, 240)
        border = (70, 100, 150, 255) if not selected else (40, 90, 180, 255)
        dpg.draw_rectangle(
            p0,
            p1,
            color=border,
            fill=fill,
            thickness=2,
            parent=drawlist,
        )
        label = self.nickname or self.name
        dpg.draw_text(
            (p0[0] + 8, p0[1] + 8),
            label,
            color=(30, 40, 55, 255),
            size=16,
            parent=drawlist,
        )

    def on_select(self) -> None:
        self._selected = True

    def on_deselect(self) -> None:
        self._selected = False

    def on_start_drag(self) -> None:
        pass

    def on_drag(self, dx: float, dy: float) -> None:
        self.move_by(dx, dy)

    def on_end_drag(self) -> None:
        pass

    def on_start_resize(self, handle: str) -> None:
        pass

    def on_resize(self, handle: str, world_x: float, world_y: float) -> None:
        self.resize_to_point(handle, world_x, world_y)

    def on_end_resize(self) -> None:
        pass

    def on_create(self) -> None:
        pass

    def on_destroy(self) -> None:
        if self._window_tag and dpg.does_item_exist(self._window_tag):
            dpg.delete_item(self._window_tag)
        self._window_tag = None

    def on_object_enter(self, other_id: str) -> None:
        if other_id not in self.children:
            self.children.append(other_id)

    def on_object_exit(self, other_id: str) -> None:
        if other_id in self.children:
            self.children.remove(other_id)

    def open_window(self, global_pos: tuple[float, float]) -> None:
        """Build or focus the FE window at a global (viewport) pixel position."""
        tag = f"megadesk::{self.canvas_id}"
        self._window_tag = tag

        if dpg.does_item_exist(tag):
            dpg.set_item_pos(tag, list(global_pos))
            try:
                dpg.focus_item(tag)
            except Exception:
                pass
            return

        def _on_close() -> None:
            self._window_tag = None

        width = max(1, int(self.width))
        height = max(1, int(self.height))
        # Migrate old thin-placard footprints to the real FE default size.
        if width <= 240 and self.spec.default_width > width:
            width = int(self.spec.default_width)
        if height <= 160 and self.spec.default_height > height:
            height = int(self.spec.default_height)
        try:
            self.spec.build(
                tag,
                pos=global_pos,
                on_close=_on_close,
                width=width,
                height=height,
                no_move=False,
                no_resize=False,
            )
        except TypeError:
            try:
                self.spec.build(tag, pos=global_pos, on_close=_on_close)
            except TypeError:
                self.spec.build(tag)

        if dpg.does_item_exist(tag):
            w = dpg.get_item_width(tag)
            h = dpg.get_item_height(tag)
            if w:
                self.width = float(w)
            if h:
                self.height = float(h)
            self.scale_x = 1.0
            self.scale_y = 1.0

    def on_double_click(self) -> None:
        # Host (DisplayEngine) should call open_window with a real pos; this is a
        # fallback that opens at the default DPG position if invoked alone.
        if self.is_gui_open():
            try:
                dpg.focus_item(self._window_tag)
            except Exception:
                pass
            return
        self.open_window(global_pos=(80.0, 80.0))
