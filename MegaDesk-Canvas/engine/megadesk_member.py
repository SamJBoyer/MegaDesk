"""Canvas-hosted shell for MegaDesk.nodes FE tools."""

from __future__ import annotations

import logging
import traceback
from typing import Any, Optional
from uuid import uuid4

import dearpygui.dearpygui as dpg

from megadesk_contracts import FeSpec

log = logging.getLogger("megadesk.canvas")

TYPE_DISCRIMINATOR = "megadesk"

MIN_SCALE = 0.15
HANDLE_HALF = 6.0  # screen-space half-size; converted via zoom when hit-testing

# Header height inside the integrated shell (world units at zoom 1; scales with view).
HEADER_H = 28.0
CLOSE_BTN = 22.0
MIN_CONTENT_W = 160.0
MIN_CONTENT_H = 120.0

CANVAS_WINDOW = "canvas_window"


def hosted_window_tag(canvas_id: str) -> str:
    """Deterministic DPG tag for a member's integrated shell."""
    return f"megadesk::{canvas_id}"


def hosted_content_tag(canvas_id: str) -> str:
    """Content parent inside the shell where FeSpec.build places widgets."""
    return f"{hosted_window_tag(canvas_id)}::content"


def destroy_hosted_window(tag: str) -> None:
    """Run FE cleanup (user_data on content or shell) then delete the shell."""
    if not tag:
        return
    content = f"{tag}::content"
    cleanup = None
    for slot in (content, tag):
        if not dpg.does_item_exist(slot):
            continue
        cleanup = dpg.get_item_user_data(slot)
        try:
            dpg.set_item_user_data(slot, None)
        except Exception:
            pass
        if callable(cleanup):
            break
    if callable(cleanup):
        try:
            cleanup()
        except Exception:
            pass
    if dpg.does_item_exist(tag):
        try:
            dpg.delete_item(tag)
        except Exception:
            pass


class MegaDeskMember:
    """Canvas member backed by an FeSpec build() callable.

    Geometry is canvas-owned. When the FE is open, MegaDesk creates one
    ``child_window`` shell under ``canvas_window`` (header + content). The FE
    only fills the content parent. Position, width, and height are world-
    anchored; screen size is ``world * view_zoom`` so open shells shrink and
    scale with canvas zoom (same transform as closed placards).
    """

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
        self._pending_redraw: bool = False
        self._pending_close: bool = False

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
        if self.is_gui_open():
            return self.width, HEADER_H + self.height
        return self.width * self.scale_x, self.height * self.scale_y

    def bounds(self) -> tuple[float, float, float, float]:
        w, h = self._shell_size_world()
        return self.position[0], self.position[1], w, h

    def content_screen_offset(self) -> tuple[float, float]:
        """Screen-pixel offset from shell origin to the content region."""
        return 0.0, HEADER_H * self._view_zoom

    def shell_height(self) -> float:
        """Full shell height in world units (header + content)."""
        return HEADER_H + self.height

    def shell_size_screen(self) -> tuple[float, float]:
        """Full shell width/height in screen pixels at the current view zoom."""
        z = self._view_zoom
        return self.width * z, (HEADER_H + self.height) * z

    def contains_point(self, x: float, y: float) -> bool:
        if self.is_gui_open():
            # Header band only — content body belongs to FE widgets.
            hw = self.width
            hh = HEADER_H
            bx, by = self.position[0], self.position[1]
            return bx <= x <= bx + hw and by <= y <= by + hh
        bx, by, bw, bh = self.bounds()
        return bx <= x <= bx + bw and by <= y <= by + bh

    def hit_close_button(self, world_x: float, world_y: float) -> bool:
        if not self.is_gui_open():
            return False
        bx, by = self.position[0], self.position[1]
        pad = 5.0
        btn = CLOSE_BTN
        right = bx + self.width
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
        """Resize open shell in world units (content size); keep scale at 1."""
        x, y = self.position[0], self.position[1]
        shell_w = self.width
        shell_h = HEADER_H + self.height
        right = x + shell_w
        bottom = y + shell_h
        min_w = MIN_CONTENT_W
        min_h = HEADER_H + MIN_CONTENT_H

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
        self.width = max(MIN_CONTENT_W, new_shell_w)
        self.height = max(MIN_CONTENT_H, new_shell_h - HEADER_H)
        self.scale_x = 1.0
        self.scale_y = 1.0

    def draw_resize_handles(self, drawlist: str | int, world_to_screen) -> None:
        x, y, w, h = self.bounds()
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
            # Open shell is a real widget tree — drawlist only shows selection
            # via draw_resize_handles. No duplicate header/content chrome.
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

    def hosted_tag(self) -> str:
        return hosted_window_tag(self.canvas_id)

    def content_tag(self) -> str:
        return hosted_content_tag(self.canvas_id)

    def close_window(self) -> None:
        """Collapse the FE to a placard (runs FE cleanup via user_data)."""
        self._pending_close = False
        tag = self._window_tag or self.hosted_tag()
        was_open = bool(self._window_tag) or dpg.does_item_exist(tag)
        destroy_hosted_window(tag)
        self._window_tag = None
        self._want_gui_open = False
        self.data["gui_open"] = False
        if was_open:
            self._pending_redraw = True

    def request_close_window(self) -> None:
        """Schedule close on the next sync tick (safe from widget callbacks)."""
        self._pending_close = True

    def open_window(self, shell_pos: tuple[float, float]) -> None:
        """Build or focus the integrated shell at a canvas-local pixel position.

        ``shell_pos`` is the shell top-left relative to ``canvas_window``.
        """
        tag = self.hosted_tag()
        content = self.content_tag()
        self._window_tag = tag
        self._want_gui_open = True
        self.data["gui_open"] = True

        if dpg.does_item_exist(tag):
            try:
                dpg.configure_item(tag, pos=list(shell_pos))
            except Exception:
                try:
                    dpg.set_item_pos(tag, list(shell_pos))
                except Exception:
                    pass
            try:
                dpg.focus_item(tag)
            except Exception:
                pass
            return

        width = max(1, int(self.width))
        height = max(1, int(self.height))
        if width <= 240 and self.spec.default_width > width:
            width = int(self.spec.default_width)
        if height <= 160 and self.spec.default_height > height:
            height = int(self.spec.default_height)
        self.width = float(width)
        self.height = float(height)
        self.scale_x = 1.0
        self.scale_y = 1.0

        # DPG widgets are screen-pixel sized; apply current zoom so the shell
        # matches the world→screen transform (sync keeps it updated on zoom).
        z = self._view_zoom
        screen_w = max(1, int(round(self.width * z)))
        screen_header = max(1, int(round(HEADER_H * z)))
        screen_h = max(1, int(round(self.height * z)))
        shell_h = max(1, screen_header + screen_h)
        label = self.nickname or self.name
        header_btn = max(8, int(round(CLOSE_BTN * z)))
        # Leave room for the close control on the right of the header row.
        title_w = max(40, screen_w - header_btn - max(8, int(round(24 * z))))

        with dpg.child_window(
            tag=tag,
            parent=CANVAS_WINDOW,
            pos=list(shell_pos),
            width=screen_w,
            height=shell_h,
            border=True,
            no_scrollbar=True,
            no_scroll_with_mouse=True,
        ):
            with dpg.group(horizontal=True, tag=f"{tag}::header"):
                dpg.add_text(label, color=(30, 40, 55, 255), wrap=title_w)
                dpg.add_spacer(width=max(1, int(round(4 * z))))
                dpg.add_button(
                    label="x",
                    width=header_btn,
                    height=max(6, header_btn - 4),
                    callback=lambda: self.request_close_window(),
                    tag=f"{tag}::close",
                )
            dpg.add_child_window(
                tag=content,
                width=-1,
                height=-1,
                border=False,
                no_scrollbar=False,
            )

        try:
            self.spec.build(
                content,
                tag_prefix=tag,
                width=width,
                height=height,
            )
        except Exception as exc:
            log.exception(
                "FeSpec.build failed for node=%s canvas_id=%s: %s",
                self.node_name,
                self.canvas_id,
                exc,
            )
            if dpg.does_item_exist(content):
                try:
                    dpg.add_text(
                        f"FE build failed: {exc}",
                        parent=content,
                        wrap=max(120, width - 20),
                        color=(200, 60, 60, 255),
                    )
                    dpg.add_text(
                        traceback.format_exc()[-1500:],
                        parent=content,
                        wrap=max(120, width - 20),
                        color=(120, 120, 130, 255),
                    )
                except Exception:
                    destroy_hosted_window(tag)
                    self._window_tag = None
                    self._want_gui_open = False
                    self.data["gui_open"] = False
                    return
            else:
                destroy_hosted_window(tag)
                self._window_tag = None
                self._want_gui_open = False
                self.data["gui_open"] = False
                return

        if not dpg.does_item_exist(tag):
            self._window_tag = None
            self._want_gui_open = False
            self.data["gui_open"] = False
            return

        # Wrap FE cleanup so host state stays consistent if cleanup runs alone.
        fe_cleanup = None
        if dpg.does_item_exist(content):
            fe_cleanup = dpg.get_item_user_data(content)

        closing = False

        def _hosted_cleanup() -> None:
            nonlocal closing
            if closing:
                return
            closing = True
            if callable(fe_cleanup):
                try:
                    fe_cleanup()
                except Exception:
                    pass
            if self._window_tag == tag:
                self._window_tag = None
            self._want_gui_open = False
            self.data["gui_open"] = False
            if dpg.does_item_exist(tag):
                try:
                    dpg.delete_item(tag)
                except Exception:
                    pass

        if dpg.does_item_exist(content):
            dpg.set_item_user_data(content, _hosted_cleanup)
        dpg.set_item_user_data(tag, _hosted_cleanup)

    def on_double_click(self) -> None:
        if self.is_gui_open():
            try:
                dpg.focus_item(self._window_tag)
            except Exception:
                pass
            return
        self.open_window(shell_pos=(80.0, 80.0))
