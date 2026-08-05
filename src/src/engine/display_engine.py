"""Display engine: parse canvas.json and render an interactive infinite board."""

from __future__ import annotations

from typing import Callable, Optional

import dearpygui.dearpygui as dpg

from engine.autocomplete import apply_completion, match_terms
from engine.base_node import BaseNode
from engine.canvas_model import CanvasMember, CanvasModel
from engine.icons import ICON_PX, get_icon_texture, get_icon_texture_for_path
from engine.megadesk_member import HEADER_H, MegaDeskMember
from engine.megadesk_registry import (
    all_fe_specs,
    fe_has_backend,
    palette_key,
    parse_palette_key,
)
from engine.registry import all_node_types, get_node_class


DRAWLIST_TAG = "canvas_drawlist"
CANVAS_WINDOW = "canvas_window"
SIDEBAR_TAG = "dropin_panel_window"
LAYER_BAR_TAG = "layer_bar_window"
TERMS_PANEL_TAG = "terms_panel_window"
TERMS_LIST_TAG = "terms_list_child"
HIERARCHY_PANEL_TAG = "hierarchy_panel_window"
HIERARCHY_LIST_TAG = "hierarchy_list_child"
CONTEXT_MENU = "canvas_context_menu"
TEXT_EDIT_MODAL = "sticky_text_modal"
TEXT_EDIT_INPUT = "sticky_text_input"
TEXT_EDIT_AC_HINT = "sticky_text_ac_hint"
TEXT_EDIT_AC_BTN = "sticky_text_ac_btn"
TEXT_EDIT_AC_ALTS = "sticky_text_ac_alts"
LAYER_RENAME_MODAL = "layer_rename_modal"
LAYER_RENAME_INPUT = "layer_rename_input"

# Floating chrome that should block Drop-in drops (geometric hit test).
_PALETTE_BLOCKING_TAGS = (
    SIDEBAR_TAG,
    LAYER_BAR_TAG,
    TERMS_PANEL_TAG,
    HIERARCHY_PANEL_TAG,
    CONTEXT_MENU,
    TEXT_EDIT_MODAL,
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
        self._edit_node_id: Optional[str] = None
        self._ac_matches: list[str] = []
        # Unity-style hierarchy: canvas_id / layer_id -> expanded
        self._hier_expanded: dict[str, bool] = {}

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
        # Prefer smaller / non-container nodes so frames don't steal child clicks
        hits: list[CanvasMember] = []
        for node in self.model.members.values():
            if not self.model.is_visible(node.canvas_id):
                continue
            if isinstance(node, MegaDeskMember):
                node.set_view_zoom(self.zoom)
            if node.contains_point(world_x, world_y):
                hits.append(node)
        if not hits:
            return None

        def rank(n: CanvasMember) -> tuple:
            _, _, w, h = n.bounds()
            return (1 if n.is_container else 0, w * h)

        hits.sort(key=rank)
        return hits[0]

    # --- rendering ---

    def redraw(self) -> None:
        if not dpg.does_item_exist(DRAWLIST_TAG):
            return
        dpg.delete_item(DRAWLIST_TAG, children_only=True)

        w = dpg.get_item_width(DRAWLIST_TAG) or 800
        h = dpg.get_item_height(DRAWLIST_TAG) or 600
        # Daytime canvas backdrop
        dpg.draw_rectangle(
            (0, 0),
            (w, h),
            color=(0, 0, 0, 0),
            fill=(248, 249, 252, 255),
            thickness=0,
            parent=DRAWLIST_TAG,
        )
        self._draw_grid(w, h)

        # Draw containers first (behind), then non-containers
        containers = []
        others = []
        for node in self.model.members.values():
            if not self.model.is_visible(node.canvas_id):
                continue
            if node.is_container:
                containers.append(node)
            else:
                others.append(node)

        for node in containers + others:
            if isinstance(node, MegaDeskMember):
                node.set_view_zoom(self.zoom)
            node.draw(
                DRAWLIST_TAG,
                self.world_to_screen,
                selected=(node.canvas_id in self.selected_ids),
            )

        # Selection chrome: outline all selected; resize handles only for a single select
        for cid in self.selected_ids:
            node = self.model.members.get(cid)
            if not node or not self.model.is_visible(cid):
                continue
            if isinstance(node, MegaDeskMember):
                node.set_view_zoom(self.zoom)
            if len(self.selected_ids) == 1:
                node.draw_resize_handles(DRAWLIST_TAG, self.world_to_screen)
            else:
                if isinstance(node, MegaDeskMember) and node.is_gui_open():
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
            if megadesk_name is not None:
                label = megadesk_name
            else:
                cls = get_node_class(self._palette_drag_type)
                label = cls.nickname if cls else self._palette_drag_type
            dpg.draw_text(
                (mx + 12, my + 12),
                f"+ {label}",
                color=(50, 50, 55, 255),
                size=14,
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

    def select(self, node: Optional[CanvasMember], *, sync_hierarchy: bool = True) -> None:
        self.select_many([node] if node else [], sync_hierarchy=sync_hierarchy)

    def select_many(self, nodes: list[CanvasMember], *, sync_hierarchy: bool = True) -> None:
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
            # Primary = smallest non-container (stable for context / single-ops)
            ranked = sorted(
                (self.model.members[cid] for cid in self.selected_ids if cid in self.model.members),
                key=lambda n: (
                    1 if n.is_container else 0,
                    n.bounds()[2] * n.bounds()[3],
                ),
            )
            self.selected_id = ranked[0].canvas_id if ranked else None
        self.redraw()
        if sync_hierarchy:
            self.refresh_hierarchy_panel()

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
        self.redraw()
        self.refresh_layer_bar()
        self.refresh_hierarchy_panel()

    def _nodes_in_marquee(self) -> list[CanvasMember]:
        """Return visible, unlocked nodes whose bounds intersect the screen marquee."""
        sx0, sy0 = self._marquee_start
        sx1, sy1 = self._marquee_end
        # Ignore click-sized marquees
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
            if isinstance(node, MegaDeskMember):
                node.set_view_zoom(self.zoom)
            bx, by, bw, bh = node.bounds()
            if bx + bw < min_x or bx > max_x or by + bh < min_y or by > max_y:
                continue
            hits.append(node)
        return hits

    def _selection_move_roots(self) -> list[str]:
        """Selected ids not carried by another selected *container* parent."""
        roots: list[str] = []
        for cid in self.selected_ids:
            node = self.model.members.get(cid)
            if not node:
                continue
            carried = False
            for pid in node.parents:
                if pid not in self.selected_ids:
                    continue
                parent = self.model.members.get(pid)
                if parent is not None and parent.is_container:
                    carried = True
                    break
            if carried:
                continue
            roots.append(cid)
        return roots

    def set_target_layer(self, layer_id: str) -> None:
        self._target_layer_id = layer_id

    # --- input handlers ---

    def on_mouse_wheel(self, sender, app_data, user_data=None) -> None:
        if not self._mouse_over_canvas():
            return
        delta = app_data  # +1 / -1 typically
        old_zoom = self.zoom
        factor = 1.1 if delta > 0 else 1 / 1.1
        new_zoom = max(self.min_zoom, min(self.max_zoom, old_zoom * factor))
        if abs(new_zoom - old_zoom) < 1e-6:
            return
        mx, my = self._draw_mouse_pos()
        # Zoom toward cursor
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
                # Press started on the Drop-in panel; ignore canvas click-to-place.
                return

            # Prefer resize handles when exactly one object is selected
            if len(self.selected_ids) == 1 and self.selected_id in self.model.members:
                sel = self.model.members[self.selected_id]
                if isinstance(sel, MegaDeskMember):
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

            # Canvas-drawn close on open MegaDesk shells
            for node in self.model.members.values():
                if not isinstance(node, MegaDeskMember) or not node.is_gui_open():
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
                # Clicking a non-selected node replaces selection; clicking inside
                # an existing multi-selection keeps the group for group-drag.
                if hit.canvas_id not in self.selected_ids:
                    self.select(hit)
                self._dragging_node = True
                self._resizing_node = False
                self._resize_handle = None
                self._marquee = False
                self._last_mouse = (mx, my)
                for cid in self._selection_move_roots():
                    node = self.model.members.get(cid)
                    if node:
                        node.on_start_drag()
            elif hit is None:
                # Empty canvas: hold MB1 and drag to draw a selection box
                self.select(None)
                self._dragging_node = False
                self._resizing_node = False
                self._resize_handle = None
                self._marquee = True
                self._marquee_start = (mx, my)
                self._marquee_end = (mx, my)
                self.redraw()
            else:
                # Locked node under cursor — clear selection, no marquee
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
            if isinstance(hit, MegaDeskMember):
                self.open_megadesk_gui(hit)
                return
            self._begin_text_edit(hit)

    def on_mouse_drag(self, sender, app_data, user_data=None) -> None:
        # app_data: [button, dx, dy] in DPG 1.x/2.x
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
                if isinstance(node, MegaDeskMember):
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
            for cid in self._selection_move_roots():
                if self.model.is_locked(cid):
                    continue
                self.model.move_node(cid, dx_w, dy_w, move_children=True)
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
                    self.model._relink_containment(self.selected_id)
                    self.model.save()
                    self.refresh_hierarchy_panel()
            elif self._dragging_node and self.selected_ids:
                for cid in self._selection_move_roots():
                    node = self.model.members.get(cid)
                    if node:
                        node.on_end_drag()
                        self.model._relink_containment(cid)
                self.model.save()
                self.refresh_hierarchy_panel()
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
        editing = (
            dpg.does_item_exist(TEXT_EDIT_MODAL) and dpg.is_item_shown(TEXT_EDIT_MODAL)
        )
        if app_data == dpg.mvKey_Escape and (
            self._palette_press_type or self._palette_drag_type
        ):
            self._cancel_palette()
            return
        # Ctrl+Enter accepts the top match. Avoid Tab — ImGui uses it for
        # focus navigation and that flickers the modal.
        if (
            editing
            and self._ac_matches
            and app_data in (dpg.mvKey_Return, dpg.mvKey_NumPadEnter)
            and dpg.is_key_down(dpg.mvKey_ModCtrl)
            and dpg.does_item_exist(TEXT_EDIT_INPUT)
            and dpg.is_item_focused(TEXT_EDIT_INPUT)
        ):
            self._accept_autocomplete()
            return
        delete_keys = {dpg.mvKey_Delete, dpg.mvKey_Back}
        if app_data in delete_keys:
            # Don't delete while editing text
            if editing:
                return
            self.delete_selected()

    def _mouse_over_canvas(self) -> bool:
        if not dpg.does_item_exist(DRAWLIST_TAG):
            return False
        # Content panels own widget input; canvas owns header chrome + rim handles
        # even when a hosted window is stacked above the drawlist.
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
            if isinstance(sel, MegaDeskMember) and sel.is_gui_open():
                sel.set_view_zoom(self.zoom)
                if sel.hit_resize_handle(wx, wy, zoom=self.zoom):
                    return True
                if sel.hit_close_button(wx, wy):
                    return True
        for node in self.model.members.values():
            if not isinstance(node, MegaDeskMember) or not node.is_gui_open():
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
            if not isinstance(node, MegaDeskMember):
                continue
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
        """Fires while the Drop-in icon is held — keep the ghost under the cursor."""
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
        if megadesk_name is not None:
            node = self.model.add_megadesk_node(
                megadesk_name, position=(wx, wy), layer_id=layer_id
            )
            self._maybe_launch_backend(megadesk_name)
            self.open_megadesk_gui(node)
        else:
            node = self.model.add_node(type_guid, position=(wx, wy), layer_id=layer_id)
        self.select(node)
        self.refresh_layer_bar()
        self.refresh_hierarchy_panel()
        self.redraw()

    def _maybe_launch_backend(self, node_name: str) -> None:
        """Ping Supervisor when the dropped MegaDesk node exposes a BE.

        The Supervisor node is special: its BE *is* the commander, so dropping
        it bootstraps ``python -m commander`` via its BeSpec instead of Redis
        ``launch_node`` (which requires the commander to already be up).
        """
        if not fe_has_backend(node_name):
            return
        try:
            from megadesk import SUPERVISOR_NODE_NAME, SupervisorClient, ensure_supervisor_running

            if node_name == SUPERVISOR_NODE_NAME:
                ensure_supervisor_running()
                return

            client = SupervisorClient(caller_identity="executive")
            if not client.redis_ok() or not client.backend_ok():
                return
            client.launch_node(node_name)
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
        # Position popup near cursor
        dpg.set_item_pos(CONTEXT_MENU, [mx, my])

    def _reset_view(self) -> None:
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.zoom = 1.0
        self.sync_megadesk_windows()
        self.redraw()

    # --- MegaDesk canvas-hosted FE panels ---

    def open_megadesk_gui(self, node: MegaDeskMember) -> None:
        """Open (or focus) a hosted content panel under the canvas header chrome."""
        node.set_view_zoom(self.zoom)
        ox, oy = self._drawlist_origin()
        sx, sy = self.world_to_screen(node.position[0], node.position[1])
        off_x, off_y = node.content_screen_offset()
        node.open_window(global_pos=(ox + sx + off_x, oy + sy + off_y))
        self.sync_megadesk_windows()

    def open_all_megadesk_guis(self) -> None:
        """Re-open FE panels for MegaDesk members that were saved as open."""
        for node in self.model.members.values():
            if not isinstance(node, MegaDeskMember):
                continue
            if node.data.get("gui_open", True) or node._want_gui_open:
                self.open_megadesk_gui(node)

    def sync_megadesk_windows(self) -> None:
        """Push canvas-owned world pos + pixel size onto hosted content panels."""
        ox, oy = self._drawlist_origin()
        needs_redraw = False
        hover_select: Optional[MegaDeskMember] = None

        for node in list(self.model.members.values()):
            if not isinstance(node, MegaDeskMember):
                continue
            node.set_view_zoom(self.zoom)
            tag = node.window_tag
            if not tag:
                continue
            if not dpg.does_item_exist(tag):
                node._window_tag = None
                node._want_gui_open = False
                node.data["gui_open"] = False
                needs_redraw = True
                continue

            if (
                hover_select is None
                and dpg.is_item_hovered(tag)
                and node.canvas_id not in self.selected_ids
                and not self.model.is_locked(node.canvas_id)
            ):
                hover_select = node

            sx, sy = self.world_to_screen(node.position[0], node.position[1])
            off_x, off_y = node.content_screen_offset()
            dpg.set_item_pos(tag, [ox + sx + off_x, oy + sy + off_y])
            w = max(1, int(node.width))
            h = max(1, int(node.height))
            try:
                dpg.configure_item(tag, width=w, height=h)
            except Exception:
                pass

        if hover_select is not None:
            self.select(hover_select, sync_hierarchy=False)
        elif needs_redraw:
            self.redraw()

    def _megadesk_window_hovered(self) -> bool:
        for node in self.model.members.values():
            if not isinstance(node, MegaDeskMember):
                continue
            tag = node.window_tag
            if tag and dpg.does_item_exist(tag) and dpg.is_item_hovered(tag):
                return True
        return False

    # --- sticky text edit + autocomplete ---

    def _begin_text_edit(self, node: CanvasMember) -> None:
        if isinstance(node, MegaDeskMember):
            self.open_megadesk_gui(node)
            return
        if not hasattr(node, "get_text"):
            node.on_double_click()
            return
        self._edit_node_id = node.canvas_id
        self._ac_matches = []
        if dpg.does_item_exist(TEXT_EDIT_MODAL):
            dpg.delete_item(TEXT_EDIT_MODAL)
        with dpg.window(
            label="Edit Text",
            modal=True,
            show=True,
            tag=TEXT_EDIT_MODAL,
            width=380,
            height=220,
            no_resize=True,
        ):
            dpg.add_input_text(
                tag=TEXT_EDIT_INPUT,
                default_value=node.get_text(),
                multiline=True,
                width=-1,
                height=90,
                callback=self._on_text_edit_changed,
            )
            # Stable widgets only — no show/hide windows (that caused flicker).
            dpg.add_text(
                "Type a glossary term for autocomplete.",
                tag=TEXT_EDIT_AC_HINT,
                color=(110, 110, 110, 255),
                wrap=350,
            )
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Insert match",
                    tag=TEXT_EDIT_AC_BTN,
                    enabled=False,
                    callback=lambda: self._accept_autocomplete(),
                )
                dpg.add_text("", tag=TEXT_EDIT_AC_ALTS, color=(90, 90, 90, 255))
            with dpg.group(horizontal=True):
                dpg.add_button(label="Save", callback=self._commit_text_edit)
                dpg.add_button(
                    label="Cancel",
                    callback=lambda: dpg.configure_item(TEXT_EDIT_MODAL, show=False),
                )
        self._refresh_autocomplete(node.get_text())
        if dpg.does_item_exist(TEXT_EDIT_INPUT):
            dpg.focus_item(TEXT_EDIT_INPUT)

    def _on_text_edit_changed(self, sender, app_data, user_data=None) -> None:
        self._refresh_autocomplete(str(app_data if app_data is not None else ""))

    def _refresh_autocomplete(self, text: str) -> None:
        self._ac_matches = match_terms(text, self.model.terms)
        if not dpg.does_item_exist(TEXT_EDIT_AC_HINT):
            return

        if not self._ac_matches:
            dpg.set_value(
                TEXT_EDIT_AC_HINT,
                "Type a glossary term for autocomplete.",
            )
            if dpg.does_item_exist(TEXT_EDIT_AC_BTN):
                dpg.configure_item(TEXT_EDIT_AC_BTN, enabled=False, label="Insert match")
            if dpg.does_item_exist(TEXT_EDIT_AC_ALTS):
                dpg.set_value(TEXT_EDIT_AC_ALTS, "")
            return

        top = self._ac_matches[0]
        extras = self._ac_matches[1:]
        dpg.set_value(
            TEXT_EDIT_AC_HINT,
            f"Suggestion: {top}   (Ctrl+Enter or Insert)",
        )
        if dpg.does_item_exist(TEXT_EDIT_AC_BTN):
            dpg.configure_item(TEXT_EDIT_AC_BTN, enabled=True, label=f"Insert “{top}”")
        if dpg.does_item_exist(TEXT_EDIT_AC_ALTS):
            dpg.set_value(
                TEXT_EDIT_AC_ALTS,
                ("Also: " + ", ".join(extras)) if extras else "",
            )

    def _accept_autocomplete(self, term: Optional[str] = None) -> None:
        if not dpg.does_item_exist(TEXT_EDIT_INPUT):
            return
        choice = term or (self._ac_matches[0] if self._ac_matches else None)
        if not choice:
            return
        current = str(dpg.get_value(TEXT_EDIT_INPUT) or "")
        completed = apply_completion(current, choice)
        dpg.set_value(TEXT_EDIT_INPUT, completed)
        self._refresh_autocomplete(completed)
        dpg.focus_item(TEXT_EDIT_INPUT)

    def _commit_text_edit(self) -> None:
        if not self._edit_node_id:
            return
        node = self.model.members.get(self._edit_node_id)
        if node and hasattr(node, "set_text"):
            node.set_text(dpg.get_value(TEXT_EDIT_INPUT))
            self.model.save()
        if dpg.does_item_exist(TEXT_EDIT_MODAL):
            dpg.configure_item(TEXT_EDIT_MODAL, show=False)
        self._edit_node_id = None
        self._ac_matches = []
        self.redraw()

    # --- sidebar / layers UI ---

    def build_sidebar(self) -> None:
        if dpg.does_item_exist(SIDEBAR_TAG):
            dpg.delete_item(SIDEBAR_TAG)

        panel_w = 228
        cols = 3
        cell_w = ICON_PX + 16

        with dpg.window(
            label="Drop-in",
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
                # (palette_key, label, description, icon_path)
                for cls in all_node_types():
                    path = cls.resolve_icon_path()
                    palette_entries.append(
                        (cls.global_guid, cls.nickname, cls.description or "", path)
                    )
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
        self.refresh_hierarchy_panel()

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
        self.refresh_hierarchy_panel()

    def _ui_remove_layer(self) -> None:
        lid = self._target_layer_id or self.model.layers[0]["id"]
        self.model.remove_layer(lid)
        self._target_layer_id = self.model.layers[0]["id"]
        self.refresh_layer_bar()
        self.refresh_hierarchy_panel()
        self.redraw()

    def _ui_toggle_visible(self, sender, app_data, user_data) -> None:
        self.model.set_layer_visible(user_data, bool(app_data))
        self.redraw()
        self.refresh_hierarchy_panel()

    def _ui_toggle_lock(self, sender, app_data, user_data) -> None:
        self.model.set_layer_locked(user_data, bool(app_data))

    def _ui_select_layer(self, sender, app_data, user_data) -> None:
        self._target_layer_id = user_data
        self.refresh_layer_bar()

    # --- Terms panel ---

    def refresh_terms_panel(self) -> None:
        if dpg.does_item_exist(TERMS_PANEL_TAG):
            dpg.delete_item(TERMS_PANEL_TAG)

        vp_w = dpg.get_viewport_client_width() or 1280
        vp_h = dpg.get_viewport_client_height() or 800
        panel_w = 300
        panel_h = max(180, int(vp_h * 0.38))
        pos_x = max(220, vp_w - panel_w - 10)
        pos_y = max(40, vp_h - panel_h - 10)

        with dpg.window(
            label="Terms",
            tag=TERMS_PANEL_TAG,
            pos=(pos_x, pos_y),
            width=panel_w,
            height=panel_h,
            no_close=True,
            no_collapse=False,
        ):
            dpg.add_text("Term / Definition", color=(90, 90, 95, 255))
            with dpg.group(horizontal=True):
                dpg.add_button(label="+ Term", callback=self._ui_add_term)
            dpg.add_separator()
            with dpg.child_window(tag=TERMS_LIST_TAG, width=-1, height=-1, border=False):
                if not self.model.terms:
                    dpg.add_text("No terms yet.", color=(120, 120, 125, 255))
                for index, entry in enumerate(self.model.terms):
                    with dpg.group(horizontal=True):
                        dpg.add_input_text(
                            default_value=entry.get("term", ""),
                            width=90,
                            hint="term",
                            user_data=("term", index),
                            callback=self._ui_term_field_changed,
                        )
                        dpg.add_input_text(
                            default_value=entry.get("definition", ""),
                            width=150,
                            hint="definition",
                            user_data=("definition", index),
                            callback=self._ui_term_field_changed,
                        )
                        dpg.add_button(
                            label="X",
                            width=24,
                            user_data=index,
                            callback=self._ui_remove_term,
                        )
                    dpg.add_spacer(height=4)

    def _ui_add_term(self) -> None:
        self.model.add_term("", "")
        self.refresh_terms_panel()

    def _ui_remove_term(self, sender, app_data, user_data) -> None:
        self.model.remove_term(int(user_data))
        self.refresh_terms_panel()

    def _ui_term_field_changed(self, sender, app_data, user_data) -> None:
        field, index = user_data
        value = str(app_data)
        if field == "term":
            self.model.update_term(int(index), term=value)
        else:
            self.model.update_term(int(index), definition=value)

    # --- Hierarchy panel (Unity-style nested tree) ---

    def refresh_hierarchy_panel(self) -> None:
        if dpg.does_item_exist(HIERARCHY_PANEL_TAG):
            dpg.delete_item(HIERARCHY_PANEL_TAG)

        vp_w = dpg.get_viewport_client_width() or 1280
        vp_h = dpg.get_viewport_client_height() or 800
        panel_w = 300
        terms_h = max(180, int(vp_h * 0.38))
        panel_h = max(200, vp_h - terms_h - 60)
        pos_x = max(220, vp_w - panel_w - 10)

        with dpg.window(
            label="Hierarchy",
            tag=HIERARCHY_PANEL_TAG,
            pos=(pos_x, 40),
            width=panel_w,
            height=panel_h,
            no_close=True,
            no_collapse=False,
        ):
            dpg.add_text("Scene hierarchy", color=(90, 90, 95, 255))
            dpg.add_separator()
            with dpg.child_window(tag=HIERARCHY_LIST_TAG, width=-1, height=-1, border=False):
                if not self.model.members and not self.model.layers:
                    dpg.add_text("Empty canvas", color=(120, 120, 125, 255))
                else:
                    for layer in self.model.layers:
                        self._draw_hierarchy_layer(layer)

    def _bind_hierarchy_handlers(self, item_id, canvas_id: Optional[str], expand_key: str) -> None:
        """Click selects a member; track open/closed so rebuilds keep expand state."""
        with dpg.item_handler_registry() as reg:
            dpg.add_item_toggled_open_handler(
                callback=self._ui_hier_toggled,
                user_data=expand_key,
            )
            if canvas_id is not None:
                dpg.add_item_clicked_handler(
                    button=dpg.mvMouseButton_Left,
                    callback=self._ui_hierarchy_select,
                    user_data=canvas_id,
                )
        dpg.bind_item_handler_registry(item_id, reg)

    def _draw_hierarchy_layer(self, layer: dict) -> None:
        lid = str(layer["id"])
        expanded = self._hier_expanded.get(lid, True)
        root_ids = self.model.root_member_ids(lid)
        visible_label = str(layer.get("name", "Layer"))
        if not layer.get("visible", True):
            visible_label = f"{visible_label} (hidden)"

        with dpg.tree_node(
            label=visible_label,
            default_open=expanded,
            open_on_arrow=True,
            span_text_width=True,
        ) as layer_node:
            self._bind_hierarchy_handlers(layer_node, canvas_id=None, expand_key=lid)
            if not root_ids:
                dpg.add_text("(empty)", color=(120, 120, 125, 255))
            else:
                seen: set[str] = set()
                for cid in root_ids:
                    self._draw_hierarchy_member(cid, seen=seen)

    def _draw_hierarchy_member(
        self, canvas_id: str, seen: Optional[set[str]] = None
    ) -> None:
        if seen is None:
            seen = set()
        if canvas_id in seen:
            return
        seen.add(canvas_id)

        node = self.model.members.get(canvas_id)
        if not node:
            return

        child_ids = [cid for cid in node.children if cid in self.model.members]
        expanded = self._hier_expanded.get(canvas_id, True)
        selected = canvas_id in self.selected_ids
        type_name = getattr(node, "global_guid", "") or node.nickname
        mark = "* " if selected else ""
        label = f"{mark}{node.nickname}  ({type_name})"

        with dpg.tree_node(
            label=label,
            default_open=expanded,
            open_on_arrow=True,
            leaf=not child_ids,
            selectable=True,
            span_text_width=True,
        ) as item:
            self._bind_hierarchy_handlers(item, canvas_id=canvas_id, expand_key=canvas_id)
            for cid in child_ids:
                self._draw_hierarchy_member(cid, seen=seen)

    def _ui_hier_toggled(self, sender, app_data, user_data) -> None:
        # app_data is True when opened
        self._hier_expanded[str(user_data)] = bool(app_data)

    def _ui_hierarchy_select(self, sender, app_data, user_data) -> None:
        node = self.model.members.get(str(user_data))
        if not node:
            return
        # Don't rebuild this panel from inside its own click callback
        self.select(node, sync_hierarchy=False)

    def on_viewport_resize(self) -> None:
        vp_w = dpg.get_viewport_client_width() or 1280
        vp_h = dpg.get_viewport_client_height() or 800
        if dpg.does_item_exist(LAYER_BAR_TAG):
            dpg.set_item_pos(LAYER_BAR_TAG, [10, max(40, vp_h - 260)])
        panel_w = 300
        terms_h = max(180, int(vp_h * 0.38))
        hier_h = max(200, vp_h - terms_h - 60)
        pos_x = max(220, vp_w - panel_w - 10)
        if dpg.does_item_exist(HIERARCHY_PANEL_TAG):
            dpg.set_item_pos(HIERARCHY_PANEL_TAG, [pos_x, 40])
            dpg.set_item_width(HIERARCHY_PANEL_TAG, panel_w)
            dpg.set_item_height(HIERARCHY_PANEL_TAG, hier_h)
        if dpg.does_item_exist(TERMS_PANEL_TAG):
            dpg.set_item_pos(TERMS_PANEL_TAG, [pos_x, max(40, vp_h - terms_h - 10)])
            dpg.set_item_width(TERMS_PANEL_TAG, panel_w)
            dpg.set_item_height(TERMS_PANEL_TAG, terms_h)
        if dpg.does_item_exist(CANVAS_WINDOW):
            dpg.set_item_width(CANVAS_WINDOW, vp_w)
            dpg.set_item_height(CANVAS_WINDOW, vp_h)
            if dpg.does_item_exist(DRAWLIST_TAG):
                dpg.set_item_width(DRAWLIST_TAG, vp_w)
                dpg.set_item_height(DRAWLIST_TAG, vp_h)
            self.sync_megadesk_windows()
            self.redraw()
