"""Alt canvas: place the three new_nodes GUIs as world-anchored windows.

Not the BaseNode host — no canvas.json, no registry. Click a type in the
sidebar to drop it at the viewport center; pan/zoom the board underneath.

Controls:
  Sidebar click  — spawn that GUI at view center
  RMB drag       — pan
  Wheel          — zoom (windows stay pixel-sized, anchored in world)
  Title-bar drag — move a GUI (world position updates on release / sync)
  Corner drag    — resize a GUI
  Window X       — remove that instance
  Delete         — remove selected (last focused) instance

Usage:
  python alt_canvas_test.py
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from uuid import uuid4

import dearpygui.dearpygui as dpg

from new_nodes import GUI_CATALOG, get_gui

CANVAS_WINDOW = "alt_canvas_window"
DRAWLIST = "alt_canvas_drawlist"
SIDEBAR = "alt_canvas_sidebar"
VP_W, VP_H = 1280, 800
SIDEBAR_W = 200


@dataclass
class PlacedGui:
    key: str
    tag: str
    world_x: float
    world_y: float
    width: int
    height: int
    label: str


class AltCanvas:
    """Pan/zoom board that hosts standalone DPG tool windows."""

    def __init__(self) -> None:
        self.pan_x = 80.0
        self.pan_y = 60.0
        self.zoom = 1.0
        self.min_zoom = 0.25
        self.max_zoom = 3.0
        self.placed: dict[str, PlacedGui] = {}
        self._panning = False
        self._last_mouse = (0.0, 0.0)
        self._selected_tag: Optional[str] = None
        self._dragging_tag: Optional[str] = None

    # --- coords ---

    def world_to_screen(self, x: float, y: float) -> tuple[float, float]:
        return x * self.zoom + self.pan_x, y * self.zoom + self.pan_y

    def screen_to_world(self, sx: float, sy: float) -> tuple[float, float]:
        return (sx - self.pan_x) / self.zoom, (sy - self.pan_y) / self.zoom

    def _drawlist_origin(self) -> tuple[float, float]:
        if dpg.does_item_exist(DRAWLIST):
            try:
                return tuple(dpg.get_item_rect_min(DRAWLIST))
            except Exception:
                pass
        return 0.0, 0.0

    def _global_mouse(self) -> tuple[float, float]:
        return tuple(dpg.get_mouse_pos(local=False))

    def _draw_mouse(self) -> tuple[float, float]:
        mx, my = self._global_mouse()
        ox, oy = self._drawlist_origin()
        return mx - ox, my - oy

    def _over_canvas(self) -> bool:
        if not dpg.does_item_exist(DRAWLIST):
            return False
        if self._over_sidebar():
            return False
        # Prefer empty canvas; don't steal events while interacting with a tool.
        for tag in self.placed:
            if dpg.does_item_exist(tag) and dpg.is_item_hovered(tag):
                return False
        return bool(dpg.is_item_hovered(DRAWLIST) or dpg.is_item_hovered(CANVAS_WINDOW))

    def _over_sidebar(self) -> bool:
        return bool(dpg.does_item_exist(SIDEBAR) and dpg.is_item_hovered(SIDEBAR))

    # --- placement ---

    def spawn(self, key: str, world_xy: Optional[tuple[float, float]] = None) -> None:
        spec = get_gui(key)
        if spec is None:
            return
        if world_xy is None:
            vp_w = dpg.get_viewport_client_width() or VP_W
            vp_h = dpg.get_viewport_client_height() or VP_H
            # Center of canvas area (exclude sidebar) in drawlist space.
            cx = SIDEBAR_W + (vp_w - SIDEBAR_W) * 0.5 - spec.width * 0.5
            cy = vp_h * 0.5 - spec.height * 0.5
            world_xy = self.screen_to_world(cx, cy)

        instance_id = uuid4().hex[:8]
        tag = f"alt::{spec.key}::{instance_id}"
        ox, oy = self._drawlist_origin()
        sx, sy = self.world_to_screen(*world_xy)
        global_pos = (ox + sx, oy + sy)

        def _on_close(t: str = tag) -> None:
            self.placed.pop(t, None)
            if self._selected_tag == t:
                self._selected_tag = None
            self.redraw()

        spec.build(
            tag,
            pos=global_pos,
            on_close=_on_close,
            no_move=False,
            no_resize=False,
        )

        self.placed[tag] = PlacedGui(
            key=spec.key,
            tag=tag,
            world_x=world_xy[0],
            world_y=world_xy[1],
            width=spec.width,
            height=spec.height,
            label=spec.label,
        )
        self._selected_tag = tag
        self.redraw()

    def remove(self, tag: str) -> None:
        if dpg.does_item_exist(tag):
            dpg.delete_item(tag)
        self.placed.pop(tag, None)
        if self._selected_tag == tag:
            self._selected_tag = None
        self.redraw()

    def _read_window_geom(self, g: PlacedGui, ox: float, oy: float) -> None:
        """Pull screen pos/size into the placed record."""
        pos = dpg.get_item_pos(g.tag)
        g.world_x, g.world_y = self.screen_to_world(pos[0] - ox, pos[1] - oy)
        w = dpg.get_item_width(g.tag)
        h = dpg.get_item_height(g.tag)
        if w:
            g.width = int(w)
        if h:
            g.height = int(h)

    def sync_window_positions(self) -> None:
        """Push world→screen, or pull screen→world while a window is dragged/resized."""
        if not self.placed:
            return
        ox, oy = self._drawlist_origin()
        left_down = dpg.is_mouse_button_down(dpg.mvMouseButton_Left)
        size_changed = False

        for g in list(self.placed.values()):
            if not dpg.does_item_exist(g.tag):
                self.placed.pop(g.tag, None)
                continue

            hovered = dpg.is_item_hovered(g.tag)
            if hovered:
                self._selected_tag = g.tag

            if hovered and left_down:
                # User may be dragging or resizing — read back into world/size.
                self._dragging_tag = g.tag
                prev = (g.width, g.height)
                self._read_window_geom(g, ox, oy)
                if (g.width, g.height) != prev:
                    size_changed = True
            elif self._dragging_tag == g.tag and not left_down:
                self._dragging_tag = None
                prev = (g.width, g.height)
                self._read_window_geom(g, ox, oy)
                if (g.width, g.height) != prev:
                    size_changed = True
            elif self._dragging_tag != g.tag:
                sx, sy = self.world_to_screen(g.world_x, g.world_y)
                dpg.set_item_pos(g.tag, [ox + sx, oy + sy])

        if size_changed:
            self.redraw()

    # --- render ---

    def redraw(self) -> None:
        if not dpg.does_item_exist(DRAWLIST):
            return
        dpg.delete_item(DRAWLIST, children_only=True)
        w = dpg.get_item_width(DRAWLIST) or VP_W
        h = dpg.get_item_height(DRAWLIST) or VP_H
        dpg.draw_rectangle(
            (0, 0),
            (w, h),
            color=(0, 0, 0, 0),
            fill=(248, 249, 252, 255),
            parent=DRAWLIST,
        )
        self._draw_grid(w, h)

        for g in self.placed.values():
            sx, sy = self.world_to_screen(g.world_x, g.world_y)
            # Footprint under the floating window (helps see anchors when zoomed).
            dpg.draw_rectangle(
                (sx - 2, sy - 2),
                (sx + g.width + 2, sy + g.height + 2),
                color=(180, 190, 210, 120),
                fill=(230, 235, 245, 40),
                thickness=1,
                parent=DRAWLIST,
            )
            dpg.draw_text(
                (sx, sy - 16),
                f"{g.label}  ({g.key})",
                size=12,
                color=(90, 100, 120, 200),
                parent=DRAWLIST,
            )

        n = len(self.placed)
        dpg.draw_text(
            (SIDEBAR_W + 12, 10),
            f"alt canvas  ·  {n} gui{'s' if n != 1 else ''}  ·  zoom {self.zoom:.2f}",
            size=14,
            color=(60, 65, 75, 220),
            parent=DRAWLIST,
        )

    def _draw_grid(self, width: float, height: float) -> None:
        step = 40.0 * self.zoom
        if step < 10:
            return
        color = (210, 214, 220, 160)
        ox = self.pan_x % step
        oy = self.pan_y % step
        x = ox
        while x < width:
            dpg.draw_line((x, 0), (x, height), color=color, thickness=1, parent=DRAWLIST)
            x += step
        y = oy
        while y < height:
            dpg.draw_line((0, y), (width, y), color=color, thickness=1, parent=DRAWLIST)
            y += step

    # --- input ---

    def on_wheel(self, sender, app_data) -> None:  # noqa: ARG001
        if not self._over_canvas():
            return
        mx, my = self._draw_mouse()
        before = self.screen_to_world(mx, my)
        delta = app_data if isinstance(app_data, (int, float)) else 0
        factor = 1.1 if delta > 0 else (1 / 1.1 if delta < 0 else 1.0)
        self.zoom = max(self.min_zoom, min(self.max_zoom, self.zoom * factor))
        after = self.screen_to_world(mx, my)
        self.pan_x += (after[0] - before[0]) * self.zoom
        self.pan_y += (after[1] - before[1]) * self.zoom
        self.sync_window_positions()
        self.redraw()

    def on_click(self, sender, app_data) -> None:  # noqa: ARG001
        button = app_data if isinstance(app_data, int) else dpg.mvMouseButton_Left
        self._last_mouse = self._draw_mouse()
        if button == dpg.mvMouseButton_Right and self._over_canvas():
            self._panning = True

    def on_drag(self, sender, app_data) -> None:  # noqa: ARG001
        mx, my = self._draw_mouse()
        dx = mx - self._last_mouse[0]
        dy = my - self._last_mouse[1]
        self._last_mouse = (mx, my)
        if self._panning:
            self.pan_x += dx
            self.pan_y += dy
            self.sync_window_positions()
            self.redraw()

    def on_release(self, sender, app_data) -> None:  # noqa: ARG001
        self._panning = False
        # Finalize any window drag into world coords.
        self.sync_window_positions()
        self.redraw()

    def on_key(self, sender, app_data) -> None:  # noqa: ARG001
        if app_data in (dpg.mvKey_Delete, dpg.mvKey_Back) and self._selected_tag:
            # Don't delete while typing in a tool field.
            focused = dpg.get_active_window()
            if focused and focused != CANVAS_WINDOW:
                # Still allow Delete when the selected tool window itself is active
                # but no text input focused — DPG doesn't expose focus easily; skip
                # if any input likely focused by checking active item type.
                try:
                    item = dpg.get_focused_item()
                    if item and dpg.get_item_type(item) in (
                        "mvAppItemType::mvInputText",
                        "mvAppItemType::mvInputInt",
                        "mvAppItemType::mvInputFloat",
                    ):
                        return
                except Exception:
                    pass
            self.remove(self._selected_tag)

    def on_viewport_resize(self) -> None:
        vp_w = dpg.get_viewport_client_width() or VP_W
        vp_h = dpg.get_viewport_client_height() or VP_H
        if dpg.does_item_exist(CANVAS_WINDOW):
            dpg.set_item_width(CANVAS_WINDOW, vp_w)
            dpg.set_item_height(CANVAS_WINDOW, vp_h)
        if dpg.does_item_exist(DRAWLIST):
            dpg.set_item_width(DRAWLIST, vp_w)
            dpg.set_item_height(DRAWLIST, vp_h)
        if dpg.does_item_exist(SIDEBAR):
            dpg.set_item_height(SIDEBAR, vp_h)
        self.sync_window_positions()
        self.redraw()

    def build_sidebar(self) -> None:
        if dpg.does_item_exist(SIDEBAR):
            dpg.delete_item(SIDEBAR)
        vp_h = dpg.get_viewport_client_height() or VP_H
        with dpg.window(
            label="Place GUI",
            tag=SIDEBAR,
            width=SIDEBAR_W,
            height=vp_h,
            pos=(0, 0),
            no_move=True,
            no_resize=True,
            no_collapse=True,
            no_close=True,
            no_title_bar=False,
        ):
            dpg.add_text("Click to place", wrap=SIDEBAR_W - 24)
            dpg.add_separator()
            for spec in GUI_CATALOG:
                dpg.add_button(
                    label=spec.label,
                    width=-1,
                    height=36,
                    user_data=spec.key,
                    callback=lambda s, a, u: self.spawn(str(u)),
                )
                dpg.add_text(
                    f"{spec.width}×{spec.height}",
                    color=(120, 125, 140, 255),
                )
                dpg.add_spacer(height=6)
            dpg.add_separator()
            dpg.add_text(
                "RMB pan · wheel zoom\n"
                "Drag title to move\n"
                "Drag corner to resize\n"
                "X or Delete to remove",
                wrap=SIDEBAR_W - 24,
                color=(100, 105, 120, 255),
            )


def _apply_theme() -> None:
    with dpg.theme() as theme:
        with dpg.theme_component(dpg.mvAll):
            dpg.add_theme_color(dpg.mvThemeCol_WindowBg, (245, 247, 250, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (250, 251, 253, 255))
            dpg.add_theme_color(dpg.mvThemeCol_PopupBg, (255, 255, 255, 250))
            dpg.add_theme_color(dpg.mvThemeCol_Border, (200, 205, 215, 255))
            dpg.add_theme_color(dpg.mvThemeCol_Text, (30, 32, 38, 255))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBg, (255, 255, 255, 255))
            dpg.add_theme_color(dpg.mvThemeCol_TitleBg, (230, 234, 240, 255))
            dpg.add_theme_color(dpg.mvThemeCol_TitleBgActive, (220, 226, 235, 255))
            dpg.add_theme_color(dpg.mvThemeCol_Button, (235, 238, 244, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (220, 228, 240, 255))
            dpg.add_theme_style(dpg.mvStyleVar_WindowRounding, 4)
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 3)
    dpg.bind_theme(theme)


def main() -> None:
    canvas = AltCanvas()

    dpg.create_context()
    _apply_theme()

    with dpg.handler_registry():
        dpg.add_mouse_wheel_handler(callback=canvas.on_wheel)
        dpg.add_mouse_click_handler(callback=canvas.on_click)
        dpg.add_mouse_drag_handler(
            button=dpg.mvMouseButton_Right,
            threshold=1,
            callback=canvas.on_drag,
        )
        dpg.add_mouse_release_handler(callback=canvas.on_release)
        dpg.add_key_press_handler(callback=canvas.on_key)

    with dpg.window(
        label="Alt Canvas",
        tag=CANVAS_WINDOW,
        no_title_bar=True,
        no_move=True,
        no_resize=True,
        no_bring_to_front_on_focus=True,
        no_scrollbar=True,
        no_scroll_with_mouse=True,
        pos=(0, 0),
        width=VP_W,
        height=VP_H,
    ):
        dpg.add_drawlist(tag=DRAWLIST, width=VP_W, height=VP_H)

    dpg.create_viewport(title="Alt Canvas — new_nodes", width=VP_W, height=VP_H)
    dpg.setup_dearpygui()
    dpg.show_viewport()
    dpg.set_primary_window(CANVAS_WINDOW, True)
    dpg.set_viewport_resize_callback(lambda *a: canvas.on_viewport_resize())

    canvas.build_sidebar()
    canvas.on_viewport_resize()
    canvas.redraw()

    # Drop one of each, staggered so they don't fully stack.
    x = 24.0
    for spec in GUI_CATALOG:
        canvas.spawn(spec.key, world_xy=(x, 24.0))
        x += spec.width + 28.0

    while dpg.is_dearpygui_running():
        canvas.sync_window_positions()
        dpg.render_dearpygui_frame()

    dpg.destroy_context()


if __name__ == "__main__":
    main()
