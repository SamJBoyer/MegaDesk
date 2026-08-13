"""Display engine: MegaDesk FE nodes hosted in a Dear PyGui node_editor."""

from __future__ import annotations

import logging
from typing import Optional

import dearpygui.dearpygui as dpg

from engine.canvas_model import CanvasModel
from engine.icons import ICON_PX, get_icon_texture_for_path
from engine.megadesk_member import (
    NODE_EDITOR,
    MegaDeskMember,
    canvas_id_from_hosted_tag,
    destroy_hosted_node,
    hosted_node_tag,
)
from engine.megadesk_registry import (
    all_fe_specs,
    fe_has_backend,
    palette_key,
    parse_palette_key,
)

log = logging.getLogger("megadesk.canvas")

CANVAS_WINDOW = "canvas_window"
SIDEBAR_TAG = "catalog_sidebar"
REF_NODE = "canvas_ref_node"
PAYLOAD_TYPE = "MEGADESK_NODE"
NODE_PADDING = (8, 8)
SUPERVISOR_PANEL_TAG = "supervisor_panel_window"


class DisplayEngine:
    def __init__(self, model: CanvasModel) -> None:
        self.model = model

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
            cid = canvas_id_from_hosted_tag(tag)
            if cid and cid in self.model.members:
                to_delete.append(cid)

        for cid in to_delete:
            self.model.delete_node(cid)
            destroy_hosted_node(hosted_node_tag(cid))

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

    def on_canvas_drop(self, sender, app_data, user_data=None) -> None:
        key = app_data
        megadesk_name = parse_palette_key(str(key)) if key else None
        if megadesk_name is None:
            return
        pos = self.editor_drop_pos()
        node = self.model.add_megadesk_node(
            megadesk_name, position=(float(pos[0]), float(pos[1]))
        )
        self._maybe_launch_backend(megadesk_name)
        node.open_on_canvas(parent=NODE_EDITOR)

    def _maybe_launch_backend(self, node_name: str) -> None:
        """XADD LAUNCHREQUEST when the dropped MegaDesk node exposes a BE."""
        if not fe_has_backend(node_name):
            return
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
            entry_id = client.launch_node(node_name, parameters="")
            log.info("LAUNCHREQUEST %s -> %s", node_name, entry_id)
        except Exception:
            log.exception("BE launch failed for MegaDesk node %s", node_name)

    # --- MegaDesk node hosting ---

    def open_megadesk_gui(self, node: MegaDeskMember) -> None:
        node.open_on_canvas(parent=NODE_EDITOR)

    def open_all_megadesk_guis(self) -> None:
        """Spawn a live node for each loaded member at its saved position."""
        for node in self.model.members.values():
            self.open_megadesk_gui(node)

    def sync_megadesk_nodes(self) -> None:
        """Sync node positions into the model; process close-button deletes."""
        pending_delete: list[str] = []
        for node in list(self.model.members.values()):
            if node.consume_pending_delete():
                pending_delete.append(node.canvas_id)
                continue
            if node.is_hosted():
                node.sync_position_from_node()
            elif dpg.does_item_exist(node.hosted_tag()):
                # Stale binding — re-attach or destroy.
                node._node_tag = node.hosted_tag()
                node.sync_position_from_node()

        for cid in pending_delete:
            self.model.delete_node(cid)
            destroy_hosted_node(hosted_node_tag(cid))

    # --- Catalog sidebar ---

    def build_sidebar(self, parent: str = SIDEBAR_TAG) -> None:
        if not dpg.does_item_exist(parent):
            return

        dpg.delete_item(parent, children_only=True)

        cols = 3
        cell_w = ICON_PX + 16

        dpg.add_text("Catalog", parent=parent, color=(40, 45, 55, 255))
        dpg.add_text(
            "Drag an icon onto the canvas",
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
        vp_w = dpg.get_viewport_client_width() or 1280
        vp_h = dpg.get_viewport_client_height() or 800
        if dpg.does_item_exist(CANVAS_WINDOW):
            dpg.set_item_width(CANVAS_WINDOW, vp_w)
            dpg.set_item_height(CANVAS_WINDOW, vp_h)
