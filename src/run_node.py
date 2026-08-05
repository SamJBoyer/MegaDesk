"""Launch any BaseNode as a standalone Dear PyGui preview.

Usage:
  python run_node.py                 # list registered types
  python run_node.py sticky          # by global_guid or nickname
  python run_node.py Sample          # nickname match (case-insensitive)
  python run_node.py path.to.mod:Cls # import an unregistered class

Controls: LMB select/drag/resize · RMB pan · wheel zoom · double-click activate
Does not load or save canvas.json.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from typing import Optional, Type
from uuid import uuid4

import dearpygui.dearpygui as dpg

from engine.base_node import BaseNode
from engine.registry import all_node_types, discover_nodes, get_node_class

DRAWLIST = "node_preview_drawlist"
WINDOW = "node_preview_window"
VP_W, VP_H = 900, 640


def _resolve_type(spec: str) -> Type[BaseNode]:
    """Resolve by guid, nickname, or ``module.path:ClassName``."""
    if ":" in spec and "/" not in spec.split(":")[0]:
        mod_name, _, cls_name = spec.partition(":")
        if mod_name and cls_name:
            mod = importlib.import_module(mod_name)
            obj = getattr(mod, cls_name)
            if not isinstance(obj, type) or not issubclass(obj, BaseNode):
                raise SystemExit(f"{spec!r} is not a BaseNode subclass")
            return obj

    key = spec.strip().lower()
    cls = get_node_class(spec) or get_node_class(key)
    if cls is not None:
        return cls

    matches = [
        t
        for t in all_node_types()
        if t.global_guid.lower() == key or t.nickname.lower() == key
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        names = ", ".join(f"{t.nickname} ({t.global_guid})" for t in matches)
        raise SystemExit(f"Ambiguous type {spec!r}: {names}")

    partial = [
        t
        for t in all_node_types()
        if key in t.global_guid.lower() or key in t.nickname.lower()
    ]
    if len(partial) == 1:
        return partial[0]
    if partial:
        names = ", ".join(f"{t.nickname} ({t.global_guid})" for t in partial)
        raise SystemExit(f"Ambiguous type {spec!r}: {names}")

    raise SystemExit(f"Unknown node type {spec!r}. Run with no args to list.")


def _list_types() -> None:
    types = sorted(all_node_types(), key=lambda t: t.nickname.lower())
    if not types:
        print("No node types registered.")
        return
    print(f"{'nickname':<20} {'guid':<24} description")
    print("-" * 72)
    for t in types:
        desc = (t.description or "").replace("\n", " ")
        if len(desc) > 40:
            desc = desc[:37] + "..."
        print(f"{t.nickname:<20} {t.global_guid:<24} {desc}")


class NodePreview:
    """Minimal pan/zoom host for a single node instance."""

    def __init__(self, node: BaseNode) -> None:
        self.node = node
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.zoom = 1.0
        self.min_zoom = 0.15
        self.max_zoom = 4.0
        self.selected = True
        self._dragging = False
        self._resizing = False
        self._resize_handle: Optional[str] = None
        self._panning = False
        self._last_mouse = (0.0, 0.0)
        node.on_select()

    def world_to_screen(self, x: float, y: float) -> tuple[float, float]:
        return x * self.zoom + self.pan_x, y * self.zoom + self.pan_y

    def screen_to_world(self, sx: float, sy: float) -> tuple[float, float]:
        return (sx - self.pan_x) / self.zoom, (sy - self.pan_y) / self.zoom

    def _draw_mouse(self) -> tuple[float, float]:
        mx, my = dpg.get_mouse_pos(local=False)
        if dpg.does_item_exist(DRAWLIST):
            try:
                ox, oy = dpg.get_item_rect_min(DRAWLIST)
                return mx - ox, my - oy
            except Exception:
                pass
        return mx, my

    def _over_canvas(self) -> bool:
        if not dpg.does_item_exist(DRAWLIST):
            return False
        return bool(dpg.is_item_hovered(DRAWLIST) or dpg.is_item_hovered(WINDOW))

    def fit_node(self, margin: float = 80.0) -> None:
        """Center the node in the viewport at a comfortable zoom."""
        x, y, w, h = self.node.bounds()
        vp_w = dpg.get_viewport_client_width() or VP_W
        vp_h = dpg.get_viewport_client_height() or VP_H
        if w <= 0 or h <= 0:
            return
        zx = (vp_w - 2 * margin) / w
        zy = (vp_h - 2 * margin) / h
        self.zoom = max(self.min_zoom, min(self.max_zoom, min(zx, zy, 1.5)))
        cx, cy = self.node.center()
        self.pan_x = vp_w * 0.5 - cx * self.zoom
        self.pan_y = vp_h * 0.5 - cy * self.zoom

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
            fill=(250, 251, 253, 255),
            parent=DRAWLIST,
        )
        self._draw_grid(w, h)
        self.node.draw(DRAWLIST, self.world_to_screen, selected=self.selected)
        if self.selected:
            self.node.draw_resize_handles(DRAWLIST, self.world_to_screen)
        label = f"{self.node.nickname}  [{self.node.global_guid}]  zoom {self.zoom:.2f}"
        dpg.draw_text((12, 10), label, size=14, color=(60, 65, 75, 220), parent=DRAWLIST)

    def _draw_grid(self, width: float, height: float) -> None:
        step = 40.0 * self.zoom
        if step < 12:
            return
        color = (210, 214, 220, 180)
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

    def on_wheel(self, sender, app_data) -> None:
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
        self.redraw()

    def on_click(self, sender, app_data) -> None:
        if not self._over_canvas():
            return
        button = app_data if isinstance(app_data, int) else dpg.mvMouseButton_Left
        mx, my = self._draw_mouse()
        self._last_mouse = (mx, my)
        wx, wy = self.screen_to_world(mx, my)

        if button == dpg.mvMouseButton_Right:
            self._panning = True
            return

        if button != dpg.mvMouseButton_Left:
            return

        if self.selected:
            handle = self.node.hit_resize_handle(wx, wy, self.zoom)
            if handle:
                self._resizing = True
                self._resize_handle = handle
                self.node.on_start_resize(handle)
                return

        if self.node.contains_point(wx, wy):
            self.selected = True
            self.node.on_select()
            self._dragging = True
            self.node.on_start_drag()
        else:
            self.selected = False
            self.node.on_deselect()
        self.redraw()

    def on_double_click(self, sender, app_data) -> None:
        if not self._over_canvas():
            return
        mx, my = self._draw_mouse()
        wx, wy = self.screen_to_world(mx, my)
        if self.node.contains_point(wx, wy):
            self.selected = True
            self.node.on_select()
            self.node.on_double_click()
            self.redraw()

    def on_drag(self, sender, app_data) -> None:
        if not self._over_canvas() and not (self._dragging or self._resizing or self._panning):
            return
        mx, my = self._draw_mouse()
        dx = mx - self._last_mouse[0]
        dy = my - self._last_mouse[1]
        self._last_mouse = (mx, my)

        if self._panning:
            self.pan_x += dx
            self.pan_y += dy
            self.redraw()
            return

        if self._resizing and self._resize_handle:
            wx, wy = self.screen_to_world(mx, my)
            self.node.on_resize(self._resize_handle, wx, wy)
            self.redraw()
            return

        if self._dragging:
            self.node.on_drag(dx / self.zoom, dy / self.zoom)
            self.redraw()

    def on_release(self, sender, app_data) -> None:
        if self._dragging:
            self.node.on_end_drag()
        if self._resizing:
            self.node.on_end_resize()
        self._dragging = False
        self._resizing = False
        self._resize_handle = None
        self._panning = False

    def on_viewport_resize(self) -> None:
        vp_w = dpg.get_viewport_client_width() or VP_W
        vp_h = dpg.get_viewport_client_height() or VP_H
        if dpg.does_item_exist(WINDOW):
            dpg.set_item_width(WINDOW, vp_w)
            dpg.set_item_height(WINDOW, vp_h)
        if dpg.does_item_exist(DRAWLIST):
            dpg.set_item_width(DRAWLIST, vp_w)
            dpg.set_item_height(DRAWLIST, vp_h)
        self.redraw()


def _apply_theme() -> None:
    with dpg.theme() as theme:
        with dpg.theme_component(dpg.mvAll):
            dpg.add_theme_color(dpg.mvThemeCol_WindowBg, (245, 247, 250, 255))
            dpg.add_theme_color(dpg.mvThemeCol_Text, (30, 32, 38, 255))
            dpg.add_theme_color(dpg.mvThemeCol_PopupBg, (255, 255, 255, 250))
            dpg.add_theme_color(dpg.mvThemeCol_Border, (200, 205, 215, 255))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBg, (255, 255, 255, 255))
            dpg.add_theme_color(dpg.mvThemeCol_Button, (235, 238, 244, 255))
            dpg.add_theme_style(dpg.mvStyleVar_WindowRounding, 4)
    dpg.bind_theme(theme)


def run_preview(node_cls: Type[BaseNode]) -> None:
    node = node_cls(position=(0.0, 0.0), canvas_id=str(uuid4()))
    node.on_create()
    preview = NodePreview(node)

    dpg.create_context()
    _apply_theme()

    with dpg.handler_registry():
        dpg.add_mouse_wheel_handler(callback=preview.on_wheel)
        dpg.add_mouse_click_handler(callback=preview.on_click)
        dpg.add_mouse_double_click_handler(callback=preview.on_double_click)
        dpg.add_mouse_drag_handler(
            button=dpg.mvMouseButton_Left,
            threshold=1,
            callback=preview.on_drag,
        )
        dpg.add_mouse_drag_handler(
            button=dpg.mvMouseButton_Right,
            threshold=1,
            callback=preview.on_drag,
        )
        dpg.add_mouse_release_handler(callback=preview.on_release)

    with dpg.window(
        label="Node Preview",
        tag=WINDOW,
        no_title_bar=True,
        no_move=True,
        no_resize=True,
        no_scrollbar=True,
        no_scroll_with_mouse=True,
        pos=(0, 0),
        width=VP_W,
        height=VP_H,
    ):
        dpg.add_drawlist(tag=DRAWLIST, width=VP_W, height=VP_H)

    title = f"Node Preview — {node.nickname} ({node.global_guid})"
    dpg.create_viewport(title=title, width=VP_W, height=VP_H)
    dpg.setup_dearpygui()
    dpg.show_viewport()
    dpg.set_primary_window(WINDOW, True)
    dpg.set_viewport_resize_callback(lambda *a: preview.on_viewport_resize())

    preview.fit_node()
    preview.redraw()

    while dpg.is_dearpygui_running():
        dpg.render_dearpygui_frame()

    node.on_destroy()
    dpg.destroy_context()


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="Preview any BaseNode as a standalone GUI.",
    )
    parser.add_argument(
        "type",
        nargs="?",
        help="global_guid, nickname, or module.path:ClassName",
    )
    parser.add_argument(
        "-l",
        "--list",
        action="store_true",
        help="list registered node types and exit",
    )
    args = parser.parse_args(argv)

    discover_nodes()

    if args.list or not args.type:
        _list_types()
        return

    node_cls = _resolve_type(args.type)
    run_preview(node_cls)


if __name__ == "__main__":
    main()
