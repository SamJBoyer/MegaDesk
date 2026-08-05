"""Canvas-hosted shell for MegaDesk.nodes FE tools (not BaseNode)."""

from __future__ import annotations

from typing import Any, Optional
from uuid import uuid4

import dearpygui.dearpygui as dpg

from megadesk import FeSpec

from engine.base_node import HANDLE_HALF, MIN_SCALE

TYPE_DISCRIMINATOR = "megadesk"

# Screen-pixel header above the hosted content panel (drag / select / close).
HEADER_H = 28.0
CLOSE_BTN = 18.0
MIN_CONTENT_W = 160.0
MIN_CONTENT_H = 120.0


class MegaDeskMember:
    """Canvas member backed by an FeSpec build() callable.

    Geometry is canvas-owned. When the FE is open, MegaDesk draws a header
    chrome + selection handles in the drawlist and push-syncs a fixed
    (no_move / no_resize / no_title_bar) content window underneath.
    Position is world-anchored; width/height stay in screen pixels (windows
    do not scale with zoom).
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
        self._view_zoom: float = 1.0
        self._want_gui_open: bool = bool(self.data.get("gui_open", True))

    @property
    def window_tag(self) -> Optional[str]:
        return self._window_tag

    def set_view_zoom(self, zoom: float) -> None:
        self._view_zoom = max(float(zoom), 1e-6)

    def is_gui_open(self) -> bool:
        return bool(self._window_tag and dpg.does_item_exist(self._window_tag))

    def to_member_dict(self) -> dict[str, Any]:
        self.data["width"] = self.width
        self.data["height"] = self.height
        self.data["node_name"] = self.name
        self.data["gui_open"] = bool(self.is_gui_open() or self._want_gui_open)
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

    # --- geometry (world) ---

    def _shell_size_world(self) -> tuple[float, float]:
        """Full shell (header + content when open, placard when closed) in world units."""
        z = self._view_zoom
        if self.is_gui_open():
            return self.width / z, (HEADER_H + self.height) / z
        return self.width * self.scale_x, self.height * self.scale_y

    def bounds(self) -> tuple[float, float, float, float]:
        w, h = self._shell_size_world()
        return self.position[0], self.position[1], w, h

    def content_screen_offset(self) -> tuple[float, float]:
        """Screen-pixel offset from shell origin to the content window top-left."""
        return 0.0, HEADER_H

    def contains_point(self, x: float, y: float) -> bool:
        if self.is_gui_open():
            # Header band only — content body belongs to the DPG window.
            z = self._view_zoom
            hw = self.width / z
            hh = HEADER_H / z
            bx, by = self.position[0], self.position[1]
            return bx <= x <= bx + hw and by <= y <= by + hh
        bx, by, bw, bh = self.bounds()
        return bx <= x <= bx + bw and by <= y <= by + bh

    def hit_close_button(self, world_x: float, world_y: float) -> bool:
        if not self.is_gui_open():
            return False
        z = self._view_zoom
        bx, by = self.position[0], self.position[1]
        # Close control in the top-right of the header (screen-pixel sized).
        pad = 5.0 / z
        btn = CLOSE_BTN / z
        right = bx + self.width / z
        cx0 = right - pad - btn
        cy0 = by + pad
        return cx0 <= world_x <= cx0 + btn and cy0 <= world_y <= cy0 + btn

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
        self._view_zoom = max(float(zoom), 1e-6)
        half = HANDLE_HALF / max(zoom, 1e-6)
        for hid, (hx, hy) in self.handle_centers().items():
            if abs(world_x - hx) <= half and abs(world_y - hy) <= half:
                return hid
        return None

    def resize_to_point(self, handle: str, world_x: float, world_y: float) -> None:
        if self.is_gui_open():
            self._resize_open(handle, world_x, world_y)
            return

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

    def _resize_open(self, handle: str, world_x: float, world_y: float) -> None:
        """Resize open shell in screen pixels (content size); keep scale at 1."""
        z = self._view_zoom
        x, y = self.position[0], self.position[1]
        shell_w = self.width / z
        shell_h = (HEADER_H + self.height) / z
        right = x + shell_w
        bottom = y + shell_h
        min_w = MIN_CONTENT_W / z
        min_h = (HEADER_H + MIN_CONTENT_H) / z

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

        new_shell_w = max(min_w, new_right - new_left)
        new_shell_h = max(min_h, new_bottom - new_top)
        self.position[0] = new_left
        self.position[1] = new_top
        self.width = max(MIN_CONTENT_W, new_shell_w * z)
        self.height = max(MIN_CONTENT_H, new_shell_h * z - HEADER_H)
        self.scale_x = 1.0
        self.scale_y = 1.0

    def draw_resize_handles(self, drawlist: str | int, world_to_screen) -> None:
        x, y, w, h = self.bounds()
        if self.is_gui_open():
            # Screen-pixel chrome (does not zoom with world bounds).
            sx, sy = world_to_screen(self.position[0], self.position[1])
            pmin = (sx - 3, sy - 3)
            pmax = (sx + self.width + 3, sy + HEADER_H + self.height + 3)
        else:
            pmin = world_to_screen(x, y)
            pmax = world_to_screen(x + w, y + h)
            pmin = (pmin[0] - 3, pmin[1] - 3)
            pmax = (pmax[0] + 3, pmax[1] + 3)
        dpg.draw_rectangle(
            pmin,
            pmax,
            color=(80, 160, 255, 220),
            fill=(0, 0, 0, 0),
            thickness=2,
            parent=drawlist,
        )
        half = HANDLE_HALF
        for hx, hy in self.handle_centers().values():
            if self.is_gui_open():
                # Map world handle centers back through pixel shell for consistency.
                sx0, sy0 = world_to_screen(self.position[0], self.position[1])
                z = self._view_zoom
                sx = sx0 + (hx - self.position[0]) * z
                sy = sy0 + (hy - self.position[1]) * z
            else:
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
        label = self.nickname or self.name
        if self.is_gui_open():
            sx, sy = world_to_screen(self.position[0], self.position[1])
            content_top = sy + HEADER_H
            border = (40, 90, 180, 255) if selected else (70, 100, 150, 255)
            header_fill = (210, 225, 245, 245) if selected else (230, 236, 245, 240)
            # Header chrome (canvas-owned drag/select/close)
            dpg.draw_rectangle(
                (sx, sy),
                (sx + self.width, content_top),
                color=border,
                fill=header_fill,
                thickness=1.5,
                parent=drawlist,
            )
            dpg.draw_text(
                (sx + 8, sy + 6),
                label,
                color=(30, 40, 55, 255),
                size=15,
                parent=drawlist,
            )
            # Close affordance
            pad = 5.0
            bx0 = sx + self.width - pad - CLOSE_BTN
            by0 = sy + pad
            dpg.draw_rectangle(
                (bx0, by0),
                (bx0 + CLOSE_BTN, by0 + CLOSE_BTN),
                color=(120, 90, 90, 220),
                fill=(245, 230, 230, 255),
                thickness=1,
                parent=drawlist,
            )
            dpg.draw_text(
                (bx0 + 4, by0 + 1),
                "x",
                color=(90, 40, 40, 255),
                size=14,
                parent=drawlist,
            )
            # Faint content footprint under the hosted window
            dpg.draw_rectangle(
                (sx, content_top),
                (sx + self.width, content_top + self.height),
                color=(180, 190, 210, 100),
                fill=(230, 235, 245, 30),
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
        self.close_window()

    def on_object_enter(self, other_id: str) -> None:
        if other_id not in self.children:
            self.children.append(other_id)

    def on_object_exit(self, other_id: str) -> None:
        if other_id in self.children:
            self.children.remove(other_id)

    def close_window(self) -> None:
        """Collapse the hosted FE to a placard (runs FE cleanup via user_data)."""
        tag = self._window_tag
        if tag and dpg.does_item_exist(tag):
            cleanup = dpg.get_item_user_data(tag)
            if callable(cleanup):
                try:
                    cleanup()
                except Exception:
                    pass
            if dpg.does_item_exist(tag):
                dpg.delete_item(tag)
        self._window_tag = None
        self._want_gui_open = False
        self.data["gui_open"] = False

    def open_window(self, global_pos: tuple[float, float]) -> None:
        """Build or focus the hosted content panel at a global pixel position.

        ``global_pos`` is the content window top-left (under the canvas header).
        """
        tag = f"megadesk::{self.canvas_id}"
        self._window_tag = tag
        self._want_gui_open = True
        self.data["gui_open"] = True

        if dpg.does_item_exist(tag):
            dpg.set_item_pos(tag, list(global_pos))
            try:
                dpg.focus_item(tag)
            except Exception:
                pass
            return

        def _on_close() -> None:
            self._window_tag = None
            self._want_gui_open = False
            self.data["gui_open"] = False

        width = max(1, int(self.width))
        height = max(1, int(self.height))
        # Migrate old thin-placard footprints to the real FE default size.
        if width <= 240 and self.spec.default_width > width:
            width = int(self.spec.default_width)
        if height <= 160 and self.spec.default_height > height:
            height = int(self.spec.default_height)
        self.width = float(width)
        self.height = float(height)
        self.scale_x = 1.0
        self.scale_y = 1.0

        self.spec.build(
            tag,
            pos=global_pos,
            on_close=_on_close,
            width=width,
            height=height,
            no_move=True,
            no_resize=True,
            no_title_bar=True,
        )

        if dpg.does_item_exist(tag):
            w = dpg.get_item_width(tag)
            h = dpg.get_item_height(tag)
            if w:
                self.width = float(w)
            if h:
                self.height = float(h)

    def on_double_click(self) -> None:
        if self.is_gui_open():
            try:
                dpg.focus_item(self._window_tag)
            except Exception:
                pass
            return
        self.open_window(global_pos=(80.0, 80.0 + HEADER_H))
