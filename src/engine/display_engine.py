"""Display engine: render an interactive infinite board of MegaDesk FE members."""

from __future__ import annotations

from typing import Callable, Optional

import dearpygui.dearpygui as dpg

from engine.canvas_model import CanvasMember, CanvasModel
from engine.icons import ICON_PX, get_icon_texture_for_path
from engine.megadesk_member import (
    HEADER_H,
    MegaDeskMember,
    destroy_hosted_window,
    hosted_window_tag,
)
from engine.megadesk_registry import (
    all_fe_specs,
    fe_has_backend,
    palette_key,
    parse_palette_key,
)


DRAWLIST_TAG = "canvas_drawlist"
CANVAS_WINDOW = "canvas_window"
SIDEBAR_TAG = "catalog_panel_window"
LAYER_BAR_TAG = "layer_bar_window"
CONTEXT_MENU = "canvas_context_menu"
LAYER_RENAME_MODAL = "layer_rename_modal"
LAYER_RENAME_INPUT = "layer_rename_input"

# Floating chrome that should block Catalog drops (geometric hit test).
_PALETTE_BLOCKING_TAGS = (
    SIDEBAR_TAG,
    LAYER_BAR_TAG,
    CONTEXT_MENU,
    LAYER_RENAME_MODAL,
)
_PALETTE_DRAG_THRESHOLD_PX = 5.0


class DisplayEngine:
    def __init__(self, model: CanvasModel) -> None:
        self.model = model
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.zoom = 1.0
        self.min_zoom = 0.15
        self.max_zoom = 4.0

        self.selected_id: Optional[str] = None
        self.selected_ids: set[str] = set()
        self._dragging_node = False
        self._resizing_node = False
        self._resize_handle: Optional[str] = None
        self._panning = False
        self._marquee = False
        self._marquee_start: tuple[float, float] = (0.0, 0.0)
        self._marquee_end: tuple[float, float] = (0.0, 0.0)
        self._last_mouse: tuple[float, float] = (0.0, 0.0)
        self._drag_start_screen: tuple[float, float] = (0.0, 0.0)
        self._right_down_pos: tuple[float, float] = (0.0, 0.0)
        self._right_dragged = False

        self._palette_drag_type: Optional[str] = None
        self._palette_press_type: Optional[str] = None
        self._palette_press_pos: tuple[float, float] = (0.0, 0.0)
        self._target_layer_id: Optional[str] = None
        self._rename_layer_id: Optional[str] = None

        self._status_cb: Optional[Callable[[str], None]] = None

    # --- coordinate transforms ---

    def world_to_screen(self, x: float, y: float) -> tuple[float, float]:
        return x * self.zoom + self.pan_x, y * self.zoom + self.pan_y

    def screen_to_world(self, sx: float, sy: float) -> tuple[float, float]:
        return (sx - self.pan_x) / self.zoom, (sy - self.pan_y) / self.zoom

    def _drawlist_origin(self) -> tuple[float, float]:
        if dpg.does_item_exist(DRAWLIST_TAG):
            try:
                return tuple(dpg.get_item_rect_min(DRAWLIST_TAG))
            except Exception:
                pass
        return 0.0, 0.0

    def _global_mouse_pos(self) -> tuple[float, float]:
        return tuple(dpg.get_mouse_pos(local=False))

    def _draw_mouse_pos(self) -> tuple[float, float]:
        """Mouse position in drawlist/canvas-local pixels."""
        mx, my = self._global_mouse_pos()
        if dpg.does_item_exist(DRAWLIST_TAG):
            try:
                ox, oy = dpg.get_item_rect_min(DRAWLIST_TAG)
                return mx - ox, my - oy
            except Exception:
                pass
        try:
            return tuple(dpg.get_drawing_mouse_pos())
        except Exception:
            return mx, my

    # --- hit testing ---

    def hit_test(self, world_x: float, world_y: float) -> Optional[CanvasMember]:
        hits: list[CanvasMember] = []
        for node in self.model.members.values():
            if not self.model.is_visible(node.canvas_id):
                continue
            node.set_view_zoom(self.zoom)
            if node.contains_point(world_x, world_y):
                hits.append(node)
        if not hits:
            return None
        hits.sort(key=lambda n: n.bounds()[2] * n.bounds()[3])
        return hits[0]

    # --- rendering ---

    def redraw(self) -> None:
        if not dpg.does_item_exist(DRAWLIST_TAG):
            return
        dpg.delete_item(DRAWLIST_TAG, children_only=True)

        w = dpg.get_item_width(DRAWLIST_TAG) or 800
        h = dpg.get_item_height(DRAWLIST_TAG) or 600
        dpg.draw_rectangle(
            (0, 0),
            (w, h),
            color=(0, 0, 0, 0),
            fill=(248, 249, 252, 255),
            thickness=0,
            parent=DRAWLIST_TAG,
        )
        self._draw_grid(w, h)

        for node in self.model.members.values():
            if not self.model.is_visible(node.canvas_id):
                continue
            node.set_view_zoom(self.zoom)
            node.draw(
                DRAWLIST_TAG,
                self.world_to_screen,
                selected=(node.canvas_id in self.selected_ids),
            )

        for cid in self.selected_ids:
            node = self.model.members.get(cid)
            if not node or not self.model.is_visible(cid):
                continue
            node.set_view_zoom(self.zoom)
            if len(self.selected_ids) == 1:
                node.draw_resize_handles(DRAWLIST_TAG, self.world_to_screen)
            else:
                if node.is_gui_open():
                    sx, sy = self.world_to_screen(node.position[0], node.position[1])
                    pmin = (sx - 3, sy - 3)
                    pmax = (sx + node.width + 3, sy + HEADER_H + node.height + 3)
                else:
                    x, y, bw, bh = node.bounds()
                    pmin = self.world_to_screen(x, y)
                    pmax = self.world_to_screen(x + bw, y + bh)
                    pmin = (pmin[0] - 3, pmin[1] - 3)
                    pmax = (pmax[0] + 3, pmax[1] + 3)
                dpg.draw_rectangle(
                    pmin,
                    pmax,
                    color=(60, 130, 220, 220),
                    fill=(0, 0, 0, 0),
                    thickness=2,
                    parent=DRAWLIST_TAG,
                )

        if self._marquee:
            x0, y0 = self._marquee_start
            x1, y1 = self._marquee_end
            dpg.draw_rectangle(
                (min(x0, x1), min(y0, y1)),
                (max(x0, x1), max(y0, y1)),
                color=(60, 130, 220, 220),
                fill=(60, 130, 220, 40),
                thickness=1.5,
                parent=DRAWLIST_TAG,
            )

        if self._palette_drag_type:
            mx, my = self._draw_mouse_pos()
            megadesk_name = parse_palette_key(self._palette_drag_type)
            label = megadesk_name or self._palette_drag_type
            dpg.draw_text(
                (mx + 12, my + 12),
                f"+ {label}",
                color=(50, 50, 55, 255),
                size=14,
                parent=DRAWLIST_TAG,
            )

        self._draw_zoom_indicator(w, h)

    def _draw_zoom_indicator(self, width: float, height: float) -> None:
        """Draw current zoom level in the lower-right corner of the canvas."""
        label = f"{self.zoom * 100:.0f}%"
        text_size = 15.0
        # Approximate glyph width for positioning without a font metrics API.
        text_w = len(label) * text_size * 0.55
        text_h = text_size
        margin = 14.0
        pad_x, pad_y = 10.0, 6.0
        x1 = width - margin
        y1 = height - margin
        x0 = x1 - text_w - pad_x * 2
        y0 = y1 - text_h - pad_y * 2
        dpg.draw_rectangle(
            (x0, y0),
            (x1, y1),
            color=(200, 205, 215, 220),
            fill=(245, 247, 250, 230),
            thickness=1,
            parent=DRAWLIST_TAG,
        )
        dpg.draw_text(
            (x0 + pad_x, y0 + pad_y),
            label,
            color=(50, 55, 65, 255),
            size=text_size,
            parent=DRAWLIST_TAG,
        )

    def _draw_grid(self, width: float, height: float) -> None:
        spacing = 40.0 * self.zoom
        if spacing < 12:
            spacing *= 4
        if spacing < 12:
            return
        origin_x = self.pan_x % spacing
        origin_y = self.pan_y % spacing
        color = (210, 214, 222, 255)
        x = origin_x
        while x < width:
            dpg.draw_line((x, 0), (x, height), color=color, thickness=1, parent=DRAWLIST_TAG)
            x += spacing
        y = origin_y
        while y < height:
            dpg.draw_line((0, y), (width, y), color=color, thickness=1, parent=DRAWLIST_TAG)
            y += spacing

    # --- selection / mutation ---

    def select(self, node: Optional[CanvasMember]) -> None:
        self.select_many([node] if node else [])

    def select_many(self, nodes: list[CanvasMember]) -> None:
        for cid in list(self.selected_ids):
            member = self.model.members.get(cid)
            if member:
                member.on_deselect()
        self.selected_ids.clear()
        self.selected_id = None
        for node in nodes:
            if not node or node.canvas_id not in self.model.members:
                continue
            self.selected_ids.add(node.canvas_id)
            node.on_select()
        if len(self.selected_ids) == 1:
            self.selected_id = next(iter(self.selected_ids))
        elif self.selected_ids:
            ranked = sorted(
                (self.model.members[cid] for cid in self.selected_ids if cid in self.model.members),
                key=lambda n: n.bounds()[2] * n.bounds()[3],
            )
            self.selected_id = ranked[0].canvas_id if ranked else None
        self.redraw()

    def delete_selected(self) -> None:
        if not self.selected_ids:
            return
        to_delete = [
            cid
            for cid in list(self.selected_ids)
            if cid in self.model.members and not self.model.is_locked(cid)
        ]
        self.selected_ids.clear()
        self.selected_id = None
        for cid in to_delete:
            self.model.delete_node(cid)
            # Guarantee no orphaned FE panel survives a cleared host binding.
            destroy_hosted_window(hosted_window_tag(cid))
        self.sync_megadesk_windows()
        self.redraw()
        self.refresh_layer_bar()

    def _nodes_in_marquee(self) -> list[CanvasMember]:
        """Return visible, unlocked nodes whose bounds intersect the screen marquee."""
        sx0, sy0 = self._marquee_start
        sx1, sy1 = self._marquee_end
        if abs(sx1 - sx0) < 4 and abs(sy1 - sy0) < 4:
            return []

        wx0, wy0 = self.screen_to_world(sx0, sy0)
        wx1, wy1 = self.screen_to_world(sx1, sy1)
        min_x, max_x = min(wx0, wx1), max(wx0, wx1)
        min_y, max_y = min(wy0, wy1), max(wy0, wy1)

        hits: list[CanvasMember] = []
        for node in self.model.members.values():
            if not self.model.is_visible(node.canvas_id):
                continue
            if self.model.is_locked(node.canvas_id):
                continue
            node.set_view_zoom(self.zoom)
            bx, by, bw, bh = node.bounds()
            if bx + bw < min_x or bx > max_x or by + bh < min_y or by > max_y:
                continue
            hits.append(node)
        return hits

    def set_target_layer(self, layer_id: str) -> None:
        self._target_layer_id = layer_id

    # --- input handlers ---

    def on_mouse_wheel(self, sender, app_data, user_data=None) -> None:
        if not self._mouse_over_canvas():
            return
        delta = app_data
        old_zoom = self.zoom
        factor = 1.1 if delta > 0 else 1 / 1.1
        new_zoom = max(self.min_zoom, min(self.max_zoom, old_zoom * factor))
        if abs(new_zoom - old_zoom) < 1e-6:
            return
        mx, my = self._draw_mouse_pos()
        wx = (mx - self.pan_x) / old_zoom
        wy = (my - self.pan_y) / old_zoom
        self.zoom = new_zoom
        self.pan_x = mx - wx * self.zoom
        self.pan_y = my - wy * self.zoom
        self.sync_megadesk_windows()
        self.redraw()

    def on_mouse_click(self, sender, app_data, user_data=None) -> None:
        button = app_data
        if not self._mouse_over_canvas():
            return

        mx, my = self._draw_mouse_pos()
        wx, wy = self.screen_to_world(mx, my)

        if button == dpg.mvMouseButton_Left:
            if self._palette_press_type or self._palette_drag_type:
                return

            if len(self.selected_ids) == 1 and self.selected_id in self.model.members:
                sel = self.model.members[self.selected_id]
                sel.set_view_zoom(self.zoom)
                if not self.model.is_locked(self.selected_id):
                    handle = sel.hit_resize_handle(wx, wy, zoom=self.zoom)
                    if handle:
                        self._resizing_node = True
                        self._resize_handle = handle
                        self._dragging_node = False
                        self._marquee = False
                        self._last_mouse = (mx, my)
                        sel.on_start_resize(handle)
                        return

            for node in self.model.members.values():
                if not node.is_gui_open():
                    continue
                if self.model.is_locked(node.canvas_id):
                    continue
                node.set_view_zoom(self.zoom)
                if node.hit_close_button(wx, wy):
                    node.close_window()
                    self.model.save()
                    self.redraw()
                    return

            hit = self.hit_test(wx, wy)
            if hit and not self.model.is_locked(hit.canvas_id):
                if hit.canvas_id not in self.selected_ids:
                    self.select(hit)
                self._dragging_node = True
                self._resizing_node = False
                self._resize_handle = None
                self._marquee = False
                self._last_mouse = (mx, my)
                for cid in self.selected_ids:
                    node = self.model.members.get(cid)
                    if node:
                        node.on_start_drag()
            elif hit is None:
                self.select(None)
                self._dragging_node = False
                self._resizing_node = False
                self._resize_handle = None
                self._marquee = True
                self._marquee_start = (mx, my)
                self._marquee_end = (mx, my)
                self.redraw()
            else:
                self.select(None)
                self._dragging_node = False
                self._resizing_node = False
                self._resize_handle = None
                self._marquee = False

        elif button == dpg.mvMouseButton_Right:
            self._right_down_pos = (mx, my)
            self._right_dragged = False
            self._panning = True
            self._last_mouse = (mx, my)

    def on_mouse_double_click(self, sender, app_data, user_data=None) -> None:
        if app_data != dpg.mvMouseButton_Left:
            return
        if not self._mouse_over_canvas():
            return
        mx, my = self._draw_mouse_pos()
        wx, wy = self.screen_to_world(mx, my)
        hit = self.hit_test(wx, wy)
        if hit and not self.model.is_locked(hit.canvas_id):
            self.select(hit)
            self.open_megadesk_gui(hit)

    def on_mouse_drag(self, sender, app_data, user_data=None) -> None:
        if not isinstance(app_data, (list, tuple)) or len(app_data) < 1:
            return
        button = app_data[0]
        mx, my = self._draw_mouse_pos()

        if button == dpg.mvMouseButton_Left and (
            self._palette_press_type or self._palette_drag_type
        ):
            self._update_palette_drag_threshold()
            if self._palette_drag_type:
                self.redraw()
            return

        if button == dpg.mvMouseButton_Right and self._panning:
            dx = mx - self._last_mouse[0]
            dy = my - self._last_mouse[1]
            if abs(mx - self._right_down_pos[0]) + abs(my - self._right_down_pos[1]) > 4:
                self._right_dragged = True
            self.pan_x += dx
            self.pan_y += dy
            self._last_mouse = (mx, my)
            self.sync_megadesk_windows()
            self.redraw()
            return

        if button == dpg.mvMouseButton_Left and self._marquee:
            self._marquee_end = (mx, my)
            self.redraw()
            return

        if button == dpg.mvMouseButton_Left and self._resizing_node and self.selected_id:
            if self.model.is_locked(self.selected_id):
                return
            node = self.model.members.get(self.selected_id)
            if node and self._resize_handle:
                node.set_view_zoom(self.zoom)
                wx, wy = self.screen_to_world(mx, my)
                node.on_resize(self._resize_handle, wx, wy)
                self._last_mouse = (mx, my)
                self.sync_megadesk_windows()
                self.redraw()
            return

        if button == dpg.mvMouseButton_Left and self._dragging_node and self.selected_ids:
            dx_s = mx - self._last_mouse[0]
            dy_s = my - self._last_mouse[1]
            dx_w = dx_s / self.zoom
            dy_w = dy_s / self.zoom
            for cid in self.selected_ids:
                if self.model.is_locked(cid):
                    continue
                self.model.move_node(cid, dx_w, dy_w)
            self._last_mouse = (mx, my)
            self.sync_megadesk_windows()
            self.redraw()

    def on_mouse_release(self, sender, app_data, user_data=None) -> None:
        button = app_data
        if button == dpg.mvMouseButton_Left:
            if self._palette_press_type or self._palette_drag_type:
                self._update_palette_drag_threshold()
                if self._palette_drag_type and self._palette_drop_allowed():
                    self._commit_palette_drop()
                else:
                    self._cancel_palette()
                return

            if self._marquee:
                mx, my = self._draw_mouse_pos()
                self._marquee_end = (mx, my)
                hits = self._nodes_in_marquee()
                self._marquee = False
                self.select_many(hits)
            elif self._resizing_node and self.selected_id:
                node = self.model.members.get(self.selected_id)
                if node:
                    node.on_end_resize()
                    self.model.save()
            elif self._dragging_node and self.selected_ids:
                for cid in self.selected_ids:
                    node = self.model.members.get(cid)
                    if node:
                        node.on_end_drag()
                self.model.save()
            self._dragging_node = False
            self._resizing_node = False
            self._resize_handle = None
            self._marquee = False

        elif button == dpg.mvMouseButton_Right:
            was_pan = self._panning
            dragged = self._right_dragged
            self._panning = False
            self._right_dragged = False
            if was_pan and not dragged and self._mouse_over_canvas():
                self._open_context_menu()

    def on_key_press(self, sender, app_data, user_data=None) -> None:
        if app_data == dpg.mvKey_Escape and (
            self._palette_press_type or self._palette_drag_type
        ):
            self._cancel_palette()
            return
        delete_keys = {dpg.mvKey_Delete, dpg.mvKey_Back}
        if app_data in delete_keys:
            self.delete_selected()

    def _mouse_over_canvas(self) -> bool:
        if not dpg.does_item_exist(DRAWLIST_TAG):
            return False
        if self._megadesk_chrome_hit():
            return True
        if self._megadesk_window_hovered():
            return False
        try:
            return dpg.is_item_hovered(DRAWLIST_TAG) or dpg.is_item_hovered(CANVAS_WINDOW)
        except Exception:
            return True

    def _megadesk_chrome_hit(self) -> bool:
        """True when the cursor is on canvas-owned chrome of an open FE shell."""
        mx, my = self._draw_mouse_pos()
        wx, wy = self.screen_to_world(mx, my)
        if len(self.selected_ids) == 1 and self.selected_id in self.model.members:
            sel = self.model.members[self.selected_id]
            if sel.is_gui_open():
                sel.set_view_zoom(self.zoom)
                if sel.hit_resize_handle(wx, wy, zoom=self.zoom):
                    return True
                if sel.hit_close_button(wx, wy):
                    return True
        for node in self.model.members.values():
            if not node.is_gui_open():
                continue
            node.set_view_zoom(self.zoom)
            if node.contains_point(wx, wy) or node.hit_close_button(wx, wy):
                return True
            if node.hit_resize_handle(wx, wy, zoom=self.zoom):
                return True
        return False

    def _item_contains_global(self, tag: str, mx: float, my: float) -> bool:
        if not dpg.does_item_exist(tag):
            return False
        try:
            if not dpg.is_item_shown(tag):
                return False
            x0, y0 = dpg.get_item_rect_min(tag)
            x1, y1 = dpg.get_item_rect_max(tag)
        except Exception:
            return False
        return x0 <= mx <= x1 and y0 <= my <= y1

    def _palette_drop_allowed(self) -> bool:
        """True when the cursor is over the canvas and not over floating chrome."""
        mx, my = self._global_mouse_pos()
        if not self._item_contains_global(DRAWLIST_TAG, mx, my):
            return False
        for tag in _PALETTE_BLOCKING_TAGS:
            if self._item_contains_global(tag, mx, my):
                return False
        for node in self.model.members.values():
            tag = node.window_tag
            if tag and self._item_contains_global(tag, mx, my):
                return False
        return True

    def _update_palette_drag_threshold(self) -> None:
        if self._palette_drag_type or not self._palette_press_type:
            return
        mx, my = self._global_mouse_pos()
        dx = mx - self._palette_press_pos[0]
        dy = my - self._palette_press_pos[1]
        if (dx * dx + dy * dy) ** 0.5 >= _PALETTE_DRAG_THRESHOLD_PX:
            self._palette_drag_type = self._palette_press_type

    def _on_palette_press(self, sender, app_data, user_data) -> None:
        self._palette_press_type = user_data
        self._palette_press_pos = self._global_mouse_pos()
        self._palette_drag_type = None
        self._dragging_node = False
        self._resizing_node = False
        self._resize_handle = None
        self._marquee = False

    def _on_palette_active(self, sender, app_data, user_data) -> None:
        """Fires while the Catalog icon is held — keep the ghost under the cursor."""
        if self._palette_press_type != user_data:
            self._on_palette_press(sender, app_data, user_data)
        was_dragging = self._palette_drag_type is not None
        self._update_palette_drag_threshold()
        if self._palette_drag_type and (
            not was_dragging or dpg.is_mouse_button_dragging(dpg.mvMouseButton_Left, 1.0)
        ):
            self.redraw()

    def _commit_palette_drop(self) -> None:
        type_guid = self._palette_drag_type
        self._palette_drag_type = None
        self._palette_press_type = None
        if not type_guid:
            self.redraw()
            return
        layer_id = self._target_layer_id or self.model.layers[0]["id"]
        layer = self.model.get_layer(layer_id)
        if layer and layer.get("locked"):
            self.redraw()
            return
        mx, my = self._draw_mouse_pos()
        wx, wy = self.screen_to_world(mx, my)
        megadesk_name = parse_palette_key(type_guid)
        if megadesk_name is None:
            self.redraw()
            return
        node = self.model.add_megadesk_node(
            megadesk_name, position=(wx, wy), layer_id=layer_id
        )
        self._maybe_launch_backend(megadesk_name)
        self.open_megadesk_gui(node)
        self.select(node)
        self.refresh_layer_bar()
        self.redraw()

    def _maybe_launch_backend(self, node_name: str) -> None:
        """Ping Supervisor when the dropped MegaDesk node exposes a BE.

        The Supervisor node is special: its BE *is* the lifecycle manager, so
        dropping it bootstraps ``python -m backend`` via its BeSpec instead of
        ``LAUNCHREQUEST`` (which requires the Supervisor BE to already be up).
        """
        if not fe_has_backend(node_name):
            return
        try:
            from megadesk import SUPERVISOR_NODE_NAME, SupervisorClient, ensure_supervisor_running

            if node_name == SUPERVISOR_NODE_NAME:
                ensure_supervisor_running()
                return

            client = SupervisorClient()
            if not client.redis_ok() or not client.backend_ok():
                return
            client.launch_node(node_name, parameters="")
        except Exception:
            pass

    def _cancel_palette(self) -> None:
        self._palette_drag_type = None
        self._palette_press_type = None
        self.redraw()

    # --- context menu ---

    def _open_context_menu(self) -> None:
        if dpg.does_item_exist(CONTEXT_MENU):
            dpg.delete_item(CONTEXT_MENU)
        mx, my = dpg.get_mouse_pos(local=False)
        with dpg.window(
            tag=CONTEXT_MENU,
            popup=True,
            show=True,
            autosize=True,
            no_title_bar=True,
            min_size=(160, 40),
        ):
            dpg.add_text("Canvas")
            dpg.add_separator()
            if self.selected_id:
                dpg.add_menu_item(label="Delete", callback=lambda: self.delete_selected())
                dpg.add_menu_item(
                    label="Deselect",
                    callback=lambda: self.select(None),
                )
            else:
                dpg.add_menu_item(
                    label="Reset View",
                    callback=self._reset_view,
                )
            dpg.add_menu_item(label="Save", callback=lambda: self.model.save())
        dpg.set_item_pos(CONTEXT_MENU, [mx, my])

    def _reset_view(self) -> None:
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.zoom = 1.0
        self.sync_megadesk_windows()
        self.redraw()

    # --- MegaDesk canvas-hosted FE panels ---

    def open_megadesk_gui(self, node: MegaDeskMember) -> None:
        """Open (or focus) the integrated FE shell at the member's screen anchor."""
        node.set_view_zoom(self.zoom)
        sx, sy = self.world_to_screen(node.position[0], node.position[1])
        node.open_window(shell_pos=(sx, sy))
        self.sync_megadesk_windows()

    def open_all_megadesk_guis(self) -> None:
        """Re-open FE shells for MegaDesk members that were saved as open."""
        for node in self.model.members.values():
            if node.data.get("gui_open", True) or node._want_gui_open:
                self.open_megadesk_gui(node)

    def sync_megadesk_windows(self) -> None:
        """Push canvas-owned world pos + pixel size onto integrated FE shells."""
        needs_redraw = False
        hover_cid: Optional[str] = None
        closed_any = False

        for node in list(self.model.members.values()):
            node.set_view_zoom(self.zoom)
            if getattr(node, "_pending_close", False):
                node.close_window()
                closed_any = True
            if getattr(node, "_pending_redraw", False):
                node._pending_redraw = False
                needs_redraw = True
            tag = node.window_tag or node.hosted_tag()
            visible = self.model.is_visible(node.canvas_id)

            if not node.window_tag:
                if dpg.does_item_exist(node.hosted_tag()):
                    # Stale shell with no host binding — destroy.
                    destroy_hosted_window(node.hosted_tag())
                    needs_redraw = True
                continue

            if not dpg.does_item_exist(tag):
                node._window_tag = None
                if node._want_gui_open:
                    node._want_gui_open = False
                    node.data["gui_open"] = False
                    needs_redraw = True
                continue

            if not visible:
                try:
                    if dpg.is_item_shown(tag):
                        dpg.hide_item(tag)
                except Exception:
                    pass
                continue

            try:
                if not dpg.is_item_shown(tag):
                    dpg.show_item(tag)
            except Exception:
                pass

            content = node.content_tag()
            if (
                hover_cid is None
                and dpg.does_item_exist(content)
                and dpg.is_item_hovered(content)
                and node.canvas_id not in self.selected_ids
                and not self.model.is_locked(node.canvas_id)
            ):
                hover_cid = node.canvas_id

            sx, sy = self.world_to_screen(node.position[0], node.position[1])
            target = [float(sx), float(sy)]
            self._push_hosted_window_geom(
                tag, target, node.width, node.shell_height()
            )

        if hover_cid is not None and hover_cid not in self.selected_ids:
            node = self.model.members.get(hover_cid)
            if node is not None:
                self.select(node)
        elif needs_redraw:
            self.redraw()

        if closed_any:
            self.model.save()

    @staticmethod
    def _push_hosted_window_geom(
        tag: str, pos: list[float], width: float, height: float
    ) -> None:
        """Force an integrated shell to the canvas-owned local rect."""
        w = max(1, int(width))
        h = max(1, int(height))
        try:
            dpg.configure_item(tag, pos=pos, width=w, height=h)
        except Exception:
            try:
                dpg.set_item_pos(tag, pos)
            except Exception:
                pass
            try:
                dpg.configure_item(tag, width=w, height=h)
            except Exception:
                pass
            return

        try:
            cur = dpg.get_item_pos(tag)
            if (
                cur is not None
                and len(cur) >= 2
                and (abs(float(cur[0]) - pos[0]) > 1.0 or abs(float(cur[1]) - pos[1]) > 1.0)
            ):
                dpg.set_item_pos(tag, pos)
        except Exception:
            pass

    def _megadesk_window_hovered(self) -> bool:
        """True when the cursor is over FE *content* (not the drag header)."""
        for node in self.model.members.values():
            content = node.content_tag() if hasattr(node, "content_tag") else None
            if content and dpg.does_item_exist(content) and dpg.is_item_hovered(content):
                return True
        return False

    # --- Catalog / layers UI ---

    def build_sidebar(self) -> None:
        if dpg.does_item_exist(SIDEBAR_TAG):
            dpg.delete_item(SIDEBAR_TAG)

        panel_w = 228
        cols = 3
        cell_w = ICON_PX + 16

        with dpg.window(
            label="Catalog",
            tag=SIDEBAR_TAG,
            pos=(10, 40),
            width=panel_w,
            height=520,
            no_close=True,
            no_collapse=False,
        ):
            dpg.add_text("Drag an icon onto the canvas", wrap=panel_w - 20)
            dpg.add_separator()

            with dpg.child_window(
                width=-1,
                height=-1,
                border=True,
                autosize_x=False,
                autosize_y=False,
            ):
                palette_entries: list[tuple[str, str, str, str | None]] = []
                for spec in all_fe_specs():
                    palette_entries.append(
                        (
                            palette_key(spec.name),
                            spec.name,
                            spec.description or "",
                            spec.icon,
                        )
                    )

                for i in range(0, len(palette_entries), cols):
                    with dpg.group(horizontal=True):
                        for key, label, description, icon_path in palette_entries[
                            i : i + cols
                        ]:
                            tex = get_icon_texture_for_path(icon_path, tag_suffix=key)
                            with dpg.group():
                                btn = dpg.add_image_button(
                                    tex,
                                    width=ICON_PX,
                                    height=ICON_PX,
                                    user_data=key,
                                )
                                handler_tag = f"palette_handlers::{key}"
                                if dpg.does_item_exist(handler_tag):
                                    dpg.delete_item(handler_tag)
                                with dpg.item_handler_registry(tag=handler_tag):
                                    dpg.add_item_activated_handler(
                                        callback=self._on_palette_press,
                                        user_data=key,
                                    )
                                    dpg.add_item_active_handler(
                                        callback=self._on_palette_active,
                                        user_data=key,
                                    )
                                dpg.bind_item_handler_registry(btn, handler_tag)
                                dpg.add_text(
                                    label,
                                    wrap=cell_w - 4,
                                    color=(40, 40, 45, 255),
                                )
                                with dpg.tooltip(btn):
                                    dpg.add_text(label)
                                    if description:
                                        dpg.add_text(
                                            description,
                                            wrap=220,
                                            color=(90, 90, 95, 255),
                                        )
                    dpg.add_spacer(height=6)

    def refresh_layer_bar(self) -> None:
        if dpg.does_item_exist(LAYER_BAR_TAG):
            dpg.delete_item(LAYER_BAR_TAG)

        vp_h = dpg.get_viewport_client_height() or 800
        with dpg.window(
            label="Layers",
            tag=LAYER_BAR_TAG,
            pos=(10, max(40, vp_h - 260)),
            width=260,
            height=230,
            no_close=True,
            no_collapse=False,
        ):
            with dpg.group(horizontal=True):
                dpg.add_button(label="+ Layer", callback=self._ui_create_layer)
                dpg.add_button(label="Rename", callback=self._ui_rename_layer)
                dpg.add_button(label="Remove", callback=self._ui_remove_layer)
            dpg.add_separator()
            dpg.add_text("Vis / Lock / Active")
            for layer in self.model.layers:
                lid = layer["id"]
                with dpg.group(horizontal=True):
                    dpg.add_checkbox(
                        label="V",
                        default_value=layer.get("visible", True),
                        user_data=lid,
                        callback=self._ui_toggle_visible,
                    )
                    dpg.add_checkbox(
                        label="L",
                        default_value=layer.get("locked", False),
                        user_data=lid,
                        callback=self._ui_toggle_lock,
                    )
                    is_active = (self._target_layer_id or self.model.layers[0]["id"]) == lid
                    label = f"{'> ' if is_active else '  '}{layer['name']}"
                    dpg.add_selectable(
                        label=label,
                        default_value=is_active,
                        user_data=lid,
                        callback=self._ui_select_layer,
                        width=160,
                    )

    def _ui_create_layer(self) -> None:
        layer = self.model.create_layer()
        self._target_layer_id = layer["id"]
        self.refresh_layer_bar()

    def _ui_rename_layer(self) -> None:
        lid = self._target_layer_id or self.model.layers[0]["id"]
        layer = self.model.get_layer(lid)
        if not layer:
            return
        self._rename_layer_id = lid
        if dpg.does_item_exist(LAYER_RENAME_MODAL):
            dpg.delete_item(LAYER_RENAME_MODAL)
        with dpg.window(
            label="Rename Layer",
            modal=True,
            show=True,
            tag=LAYER_RENAME_MODAL,
            width=280,
            height=120,
            no_resize=True,
        ):
            dpg.add_input_text(
                tag=LAYER_RENAME_INPUT,
                default_value=layer["name"],
                width=-1,
            )
            with dpg.group(horizontal=True):
                dpg.add_button(label="OK", callback=self._commit_layer_rename)
                dpg.add_button(
                    label="Cancel",
                    callback=lambda: dpg.configure_item(LAYER_RENAME_MODAL, show=False),
                )

    def _commit_layer_rename(self) -> None:
        if self._rename_layer_id:
            self.model.rename_layer(self._rename_layer_id, dpg.get_value(LAYER_RENAME_INPUT))
        if dpg.does_item_exist(LAYER_RENAME_MODAL):
            dpg.configure_item(LAYER_RENAME_MODAL, show=False)
        self.refresh_layer_bar()

    def _ui_remove_layer(self) -> None:
        lid = self._target_layer_id or self.model.layers[0]["id"]
        self.model.remove_layer(lid)
        self._target_layer_id = self.model.layers[0]["id"]
        self.refresh_layer_bar()
        self.redraw()

    def _ui_toggle_visible(self, sender, app_data, user_data) -> None:
        self.model.set_layer_visible(user_data, bool(app_data))
        self.redraw()

    def _ui_toggle_lock(self, sender, app_data, user_data) -> None:
        self.model.set_layer_locked(user_data, bool(app_data))

    def _ui_select_layer(self, sender, app_data, user_data) -> None:
        self._target_layer_id = user_data
        self.refresh_layer_bar()

    def on_viewport_resize(self) -> None:
        vp_w = dpg.get_viewport_client_width() or 1280
        vp_h = dpg.get_viewport_client_height() or 800
        if dpg.does_item_exist(LAYER_BAR_TAG):
            dpg.set_item_pos(LAYER_BAR_TAG, [10, max(40, vp_h - 260)])
        if dpg.does_item_exist(CANVAS_WINDOW):
            dpg.set_item_width(CANVAS_WINDOW, vp_w)
            dpg.set_item_height(CANVAS_WINDOW, vp_h)
            if dpg.does_item_exist(DRAWLIST_TAG):
                dpg.set_item_width(DRAWLIST_TAG, vp_w)
                dpg.set_item_height(DRAWLIST_TAG, vp_h)
            self.sync_megadesk_windows()
            self.redraw()
