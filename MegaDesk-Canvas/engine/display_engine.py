"""Display engine: MegaDesk FE nodes hosted in a Dear PyGui node_editor."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional

import dearpygui.dearpygui as dpg

from megadesk_contracts import FeSpec

from engine.graph_model import GraphModel, available_graphs
from engine.icons import ICON_PX, get_icon_texture_for_path
from engine.megadesk_member import (
    NODE_EDITOR,
    MegaDeskMember,
    destroy_hosted_node,
    hosted_node_tag,
    member_id_from_hosted_tag,
)
from engine.megadesk_registry import (
    all_fe_specs,
    palette_key,
    parse_palette_key,
)

log = logging.getLogger("megadesk.canvas")

GRAPH_WINDOW = "graph_window"
SIDEBAR_TAG = "catalog_sidebar"
CATALOG_BODY_TAG = "catalog_sidebar::body"
CATALOG_TOGGLE_TAG = "catalog_sidebar::toggle"
CANVAS_BODY_TAG = "canvas_body"
EDITOR_HOST_TAG = "graph_editor_host"
REF_NODE = "graph_ref_node"
PAYLOAD_TYPE = "MEGADESK_NODE"
NODE_PADDING = (8, 8)
SUPERVISOR_PANEL_TAG = "supervisor_panel_window"
SUPERVISOR_BODY_TAG = "supervisor_panel_window::body"
SUPERVISOR_TOGGLE_TAG = "supervisor_panel_window::toggle"

CATALOG_WIDTH = 240
SUPERVISOR_WIDTH = 360
COLLAPSED_PANEL_WIDTH = 22
_WINDOW_PAD = 16


class DisplayEngine:
    def __init__(self, model: GraphModel) -> None:
        self.model = model
        # Set by the graph bar so it can re-read the model after a load / save.
        self.on_graph_changed: Optional[Callable[[], None]] = None
        self.graph_bar = None
        self.catalog_expanded = True
        self.supervisor_expanded = True
        self.supervisor_enabled = False
        self.on_member_selected: Optional[Callable[[str, tuple[str, ...]], None]] = None
        self._selected_member_id: Optional[str] = None

    # --- drop position (Canvas2 REF_NODE technique) ---

    def editor_drop_pos(self) -> list[float]:
        """Map global mouse to node_editor grid coordinates."""
        mouse = dpg.get_mouse_pos(local=False)
        children = dpg.get_item_children(NODE_EDITOR, slot=1) or []
        ref = children[0] if children else REF_NODE
        if ref == REF_NODE:
            dpg.show_item(REF_NODE)
            dpg.split_frame()
            ref_screen = dpg.get_item_rect_min(REF_NODE)
            ref_grid = dpg.get_item_pos(REF_NODE)
            dpg.hide_item(REF_NODE)
        else:
            ref_screen = dpg.get_item_rect_min(ref)
            ref_grid = dpg.get_item_pos(ref)

        return [
            mouse[0] - (ref_screen[0] - NODE_PADDING[0]) + ref_grid[0],
            mouse[1] - (ref_screen[1] - NODE_PADDING[1]) + ref_grid[1],
        ]

    # --- selection / delete ---

    def delete_selected(self) -> None:
        if not dpg.does_item_exist(NODE_EDITOR):
            return
        try:
            selected = list(dpg.get_selected_nodes(NODE_EDITOR) or [])
        except Exception:
            selected = []
        if not selected:
            return

        to_delete: list[str] = []
        for tag in selected:
            if tag == REF_NODE:
                continue
            member_id = member_id_from_hosted_tag(tag)
            if member_id and member_id in self.model.members:
                to_delete.append(member_id)

        for member_id in to_delete:
            self.model.delete_node(member_id)
            destroy_hosted_node(hosted_node_tag(member_id))

        try:
            dpg.clear_selected_nodes(NODE_EDITOR)
        except Exception:
            pass

    def on_key_press(self, sender, app_data, user_data=None) -> None:
        if app_data != dpg.mvKey_Delete:
            return
        # Don't delete nodes while typing in an input.
        try:
            focused = dpg.get_focused_item()
        except Exception:
            focused = None
        if focused is not None:
            try:
                itype = str(dpg.get_item_type(focused))
                if "InputText" in itype or "InputInt" in itype or "InputFloat" in itype:
                    return
            except Exception:
                pass
        self.delete_selected()

    # --- Catalog drop ---

    def on_graph_drop(self, sender, app_data, user_data=None) -> None:
        key = app_data
        megadesk_name = parse_palette_key(str(key)) if key else None
        if megadesk_name is None:
            return
        pos = self.editor_drop_pos()
        member = self.model.add_megadesk_node(
            megadesk_name, position=(float(pos[0]), float(pos[1]))
        )
        self._maybe_launch_backend(member.spec)
        member.open_on_graph(parent=NODE_EDITOR)
        self._notify_graph_changed()

    def _maybe_launch_backend(self, spec: Optional[FeSpec]) -> None:
        """XADD LAUNCHREQUEST for each BE the hosted FeSpec lists.

        The spec is the member's own — built around its graph parameters — so
        ``backend_parameters`` is whatever subset that node wants its BE to
        start with.
        """
        endpoints = tuple(spec.backends) if spec is not None else ()
        if not endpoints or spec is None:
            return
        node_name = spec.name
        parameters = dict(spec.backend_parameters or {})
        try:
            from megadesk_contracts import SupervisorClient

            client = SupervisorClient()
            if not client.redis_ok():
                log.warning(
                    "Skip BE launch for %s: Redis not reachable at %s",
                    node_name,
                    client.redis_url,
                )
                return
            if not client.backend_ok():
                log.warning(
                    "Skip BE launch for %s: Supervisor BE not alive "
                    "(canvas should start it on launch; use Supervisor panel Start BE)",
                    node_name,
                )
                return
            already = {
                (e.get("node_endpoint") or "")
                for e in client.list_running()
            }
            for endpoint in endpoints:
                if endpoint in already:
                    log.info("BE %s already alive; skip LAUNCHREQUEST", endpoint)
                    continue
                entry_id = client.launch_node(endpoint, parameters=parameters)
                log.info("LAUNCHREQUEST %s -> %s", endpoint, entry_id)
        except Exception:
            log.exception("BE launch failed for MegaDesk node %s", node_name)

    # --- MegaDesk node hosting ---

    def host_member(self, member: MegaDeskMember) -> None:
        member.open_on_graph(parent=NODE_EDITOR)

    def host_all_members(self) -> None:
        """Host each of the graph's FEs and start the BEs their FeSpecs list."""
        for member in self.model.members.values():
            self.host_member(member)
            self._maybe_launch_backend(member.spec)

    def sync_members(self) -> None:
        """Sync node positions into the model; process close-button deletes."""
        pending_delete: list[str] = []
        for member in list(self.model.members.values()):
            if member.consume_pending_delete():
                pending_delete.append(member.member_id)
                continue
            if member.is_hosted():
                member.sync_position_from_node()
            elif dpg.does_item_exist(member.hosted_tag()):
                # Stale binding — re-attach or destroy.
                member._node_tag = member.hosted_tag()
                member.sync_position_from_node()

        for member_id in pending_delete:
            if member_id == self._selected_member_id:
                self._selected_member_id = None
            self.model.delete_node(member_id)
            destroy_hosted_node(hosted_node_tag(member_id))
        if pending_delete:
            self._notify_graph_changed()
        self._sync_canvas_selection()

    # --- graph file operations (driven by the graph bar) ---

    def _notify_graph_changed(self) -> None:
        if self.on_graph_changed is None:
            return
        try:
            self.on_graph_changed()
        except Exception:
            log.exception("Graph bar refresh failed")

    def graph_path(self) -> Path:
        return self.model.path

    def graph_choices(self) -> list[Path]:
        """Graphs offered in the bar: the graphs directory plus the open file."""
        choices = available_graphs()
        current = self.model.path
        if current not in choices:
            choices.append(current)
        return choices

    def load_graph(self, path: Path) -> None:
        """Open another graph: validate, swap the board, relaunch its BEs.

        ``GraphError`` propagates so the bar can show why a file was refused;
        the open graph is still intact when it does.
        """
        self.model.load_from(path)
        self.host_all_members()
        self._notify_graph_changed()

    def save_graph(self) -> None:
        self.sync_members()
        self.model.save()
        self._notify_graph_changed()

    def save_graph_as(self, path: Path) -> None:
        self.sync_members()
        self.model.save_as(path)
        self._notify_graph_changed()

    def delete_graph(self) -> None:
        """Delete the open graph's file, then fall back to another one.

        With nothing left to fall back to the board is cleared, and the model
        keeps pointing at the deleted path so a later Save recreates it.
        """
        self.model.delete_file()
        remaining = [p for p in available_graphs() if p != self.model.path]
        if remaining:
            self.load_graph(remaining[0])
            return
        self.model.close_all()
        self._notify_graph_changed()

    def capture_parameters(self) -> dict[str, dict[str, str]]:
        """Push what the sub-GUIs currently show into the graph, and save it."""
        captured = self.model.capture_parameters()
        self.model.save()
        self._notify_graph_changed()
        return captured

    def _sync_canvas_selection(self) -> None:
        """When the operator selects a hosted node, show that node's Supervisor log."""
        if not dpg.does_item_exist(NODE_EDITOR):
            return
        try:
            selected = list(dpg.get_selected_nodes(NODE_EDITOR) or [])
        except Exception:
            selected = []
        member_id: Optional[str] = None
        for tag in selected:
            if tag == REF_NODE:
                continue
            parsed = member_id_from_hosted_tag(tag)
            if parsed and parsed in self.model.members:
                member_id = parsed
                break
        if member_id == self._selected_member_id:
            return
        self._selected_member_id = member_id
        if member_id is None or self.on_member_selected is None:
            return
        member = self.model.members[member_id]
        backends = tuple(member.spec.backends or ())
        self.on_member_selected(member.name, backends)

    def notify_member_clicked(self, member_id: str) -> None:
        """Show logs for a canvas member (same path as a node_editor selection)."""
        if member_id not in self.model.members:
            return
        self._selected_member_id = member_id
        if self.on_member_selected is None:
            return
        member = self.model.members[member_id]
        self.on_member_selected(member.name, tuple(member.spec.backends or ()))

    # --- Catalog / Supervisor chrome ---

    def toggle_catalog(self) -> None:
        self.catalog_expanded = not self.catalog_expanded
        self.relayout_chrome()

    def toggle_supervisor(self) -> None:
        if not self.supervisor_enabled:
            return
        self.supervisor_expanded = not self.supervisor_expanded
        self.relayout_chrome()

    def relayout_chrome(self) -> None:
        """Size the left Catalog, editor, and right Supervisor as a single row."""
        vp_w = dpg.get_viewport_client_width() or 1280
        vp_h = dpg.get_viewport_client_height() or 800
        if dpg.does_item_exist(GRAPH_WINDOW):
            dpg.set_item_width(GRAPH_WINDOW, vp_w)
            dpg.set_item_height(GRAPH_WINDOW, vp_h)

        cat_w = CATALOG_WIDTH if self.catalog_expanded else COLLAPSED_PANEL_WIDTH
        sup_w = 0
        if self.supervisor_enabled and dpg.does_item_exist(SUPERVISOR_PANEL_TAG):
            sup_w = (
                SUPERVISOR_WIDTH if self.supervisor_expanded else COLLAPSED_PANEL_WIDTH
            )
        editor_w = max(160, int(vp_w) - cat_w - sup_w - _WINDOW_PAD)

        bar_h = 0
        if dpg.does_item_exist("graph_bar"):
            try:
                bar_h = int(dpg.get_item_height("graph_bar") or 0)
            except Exception:
                bar_h = 32
        body_h = max(120, int(vp_h) - bar_h - _WINDOW_PAD)

        if dpg.does_item_exist(SIDEBAR_TAG):
            dpg.configure_item(SIDEBAR_TAG, width=cat_w, height=body_h)
        if dpg.does_item_exist(CATALOG_BODY_TAG):
            dpg.configure_item(CATALOG_BODY_TAG, show=self.catalog_expanded)
        if dpg.does_item_exist(CATALOG_TOGGLE_TAG):
            dpg.configure_item(
                CATALOG_TOGGLE_TAG, label="<" if self.catalog_expanded else ">"
            )
        if dpg.does_item_exist(EDITOR_HOST_TAG):
            dpg.configure_item(EDITOR_HOST_TAG, width=editor_w, height=body_h)
        if dpg.does_item_exist(SUPERVISOR_PANEL_TAG):
            dpg.configure_item(
                SUPERVISOR_PANEL_TAG,
                width=sup_w,
                height=body_h,
                show=True,
            )
        if dpg.does_item_exist(SUPERVISOR_BODY_TAG):
            dpg.configure_item(SUPERVISOR_BODY_TAG, show=self.supervisor_expanded)
        if dpg.does_item_exist(SUPERVISOR_TOGGLE_TAG):
            dpg.configure_item(
                SUPERVISOR_TOGGLE_TAG,
                label=">" if self.supervisor_expanded else "<",
            )

    # --- Catalog sidebar ---

    def build_sidebar(self, parent: Optional[str] = None) -> None:
        if parent is None:
            parent = (
                CATALOG_BODY_TAG
                if dpg.does_item_exist(CATALOG_BODY_TAG)
                else SIDEBAR_TAG
            )
        if not dpg.does_item_exist(parent):
            return

        dpg.delete_item(parent, children_only=True)

        cols = 3
        cell_w = ICON_PX + 16

        dpg.add_text("Catalog", parent=parent, color=(40, 45, 55, 255))
        dpg.add_text(
            "Drag an icon onto the graph",
            parent=parent,
            wrap=220,
            color=(90, 95, 105, 255),
        )
        dpg.add_separator(parent=parent)

        with dpg.child_window(
            parent=parent,
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
                            )
                            with dpg.drag_payload(
                                parent=btn,
                                drag_data=key,
                                payload_type=PAYLOAD_TYPE,
                            ):
                                dpg.add_text(f"Drop: {label}")
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

    def on_viewport_resize(self) -> None:
        self.relayout_chrome()
