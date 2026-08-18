"""Graph-hosted MegaDesk FE as a native Dear PyGui node."""

from __future__ import annotations

import logging
import traceback
from typing import Any, Mapping, Optional
from uuid import uuid4

import dearpygui.dearpygui as dpg

from megadesk_contracts import FeSpec, normalize_parameters

log = logging.getLogger("megadesk.canvas")

TYPE_DISCRIMINATOR = "megadesk"

NODE_EDITOR = "graph_editor"
MIN_CONTENT_W = 160
MIN_CONTENT_H = 120


def hosted_node_tag(member_id: str) -> str:
    """Deterministic DPG tag for a member's node."""
    return f"megadesk::{member_id}"


def hosted_content_tag(member_id: str) -> str:
    """Content parent inside the node where FeSpec.build places widgets."""
    return f"{hosted_node_tag(member_id)}::content"


def member_id_from_hosted_tag(tag: str | int) -> Optional[str]:
    """Parse member_id from a hosted node tag, or None if not ours."""
    s = str(tag)
    prefix = "megadesk::"
    if not s.startswith(prefix):
        return None
    rest = s[len(prefix) :]
    if "::" in rest:
        return None
    return rest or None


def destroy_hosted_node(tag: str) -> None:
    """Run FE cleanup (user_data on content or node) then delete the node."""
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
    """Graph member backed by an FeSpec build() callable.

    Hosted as a native ``dpg.node`` inside ``NODE_EDITOR``. The FE fills a
    content parent inside a static node attribute via ``FeSpec.build``.
    """

    def __init__(
        self,
        spec: FeSpec,
        *,
        member_id: Optional[str] = None,
        position: Optional[tuple[float, float]] = None,
        parameters: Optional[Mapping[str, str]] = None,
        data: Optional[dict[str, Any]] = None,
    ) -> None:
        self.spec = spec
        self.name = spec.name
        self.nickname = spec.name
        self.description = spec.description
        self.member_id: str = member_id or str(uuid4())
        self.position: list[float] = list(position or (0.0, 0.0))
        self.parameters: dict[str, str] = normalize_parameters(
            parameters, spec.parameters or None
        )
        self.data: dict[str, Any] = dict(data or {})
        self.width: float = float(self.data.get("width", spec.default_width))
        self.height: float = float(self.data.get("height", spec.default_height))
        self._node_tag: Optional[str] = None
        self._pending_delete: bool = False

    def is_hosted(self) -> bool:
        return bool(self._node_tag and dpg.does_item_exist(self._node_tag))

    def to_member_dict(self) -> dict[str, Any]:
        self.data["width"] = self.width
        self.data["height"] = self.height
        self.data["node_name"] = self.name
        return {
            "member_id": self.member_id,
            "type": TYPE_DISCRIMINATOR,
            "nickname": self.nickname,
            "node_name": self.name,
            "position": [float(self.position[0]), float(self.position[1])],
            "parameters": dict(self.parameters),
            "data": dict(self.data),
        }

    @classmethod
    def from_member_dict(cls, member: dict[str, Any], spec: FeSpec) -> "MegaDeskMember":
        return cls(
            spec,
            member_id=member.get("member_id"),
            position=tuple(member.get("position", (0.0, 0.0))),
            parameters=member.get("parameters"),
            data=dict(member.get("data") or {}),
        )

    def set_parameters(self, values: Mapping[str, str]) -> dict[str, str]:
        """Replace this member's saved parameters, keeping declared names only."""
        self.parameters = normalize_parameters(values, self.spec.parameters or None)
        return dict(self.parameters)

    def read_live_parameters(self) -> dict[str, str]:
        """Current parameter values as the hosted FE reports them.

        Returns ``{}`` for nodes that declare no parameters or expose no reader —
        capturing then leaves whatever the graph already had.
        """
        reader = self.spec.read_parameters
        if reader is None or not self.spec.parameters:
            return {}
        try:
            values = reader(self.hosted_tag())
        except Exception:
            log.exception("read_parameters failed for node=%s", self.name)
            return {}
        return normalize_parameters(values, self.spec.parameters)

    def sync_position_from_node(self) -> None:
        """Read editor-grid position from the live node into ``member.position``."""
        tag = self._node_tag or self.hosted_tag()
        if not dpg.does_item_exist(tag):
            return
        try:
            pos = dpg.get_item_pos(tag)
        except Exception:
            return
        if pos is None or len(pos) < 2:
            return
        self.position[0] = float(pos[0])
        self.position[1] = float(pos[1])

    def hosted_tag(self) -> str:
        return hosted_node_tag(self.member_id)

    def content_tag(self) -> str:
        return hosted_content_tag(self.member_id)

    def destroy_node(self) -> None:
        """Tear down the hosted node and run FE cleanup."""
        tag = self._node_tag or self.hosted_tag()
        destroy_hosted_node(tag)
        self._node_tag = None

    def open_on_graph(self, *, parent: str = NODE_EDITOR) -> None:
        """Create the live FE node at ``self.position`` (no-op if already hosted)."""
        tag = self.hosted_tag()
        content = self.content_tag()

        if dpg.does_item_exist(tag):
            self._node_tag = tag
            try:
                dpg.configure_item(tag, pos=[float(self.position[0]), float(self.position[1])])
            except Exception:
                try:
                    dpg.set_item_pos(tag, [float(self.position[0]), float(self.position[1])])
                except Exception:
                    pass
            return

        width = max(MIN_CONTENT_W, int(self.width))
        height = max(MIN_CONTENT_H, int(self.height))
        if width <= 240 and self.spec.default_width > width:
            width = int(self.spec.default_width)
        if height <= 160 and self.spec.default_height > height:
            height = int(self.spec.default_height)
        self.width = float(width)
        self.height = float(height)

        label = self.nickname or self.name
        pos = [float(self.position[0]), float(self.position[1])]

        with dpg.node(label=label, pos=pos, tag=tag, parent=parent):
            with dpg.node_attribute(attribute_type=dpg.mvNode_Attr_Static):
                with dpg.group(horizontal=True):
                    dpg.add_text(label, color=(40, 45, 55, 255))
                    dpg.add_spacer(width=8)
                    dpg.add_button(
                        label="x",
                        width=22,
                        height=18,
                        callback=lambda: self._on_close_clicked(),
                        tag=f"{tag}::close",
                    )
                dpg.add_child_window(
                    tag=content,
                    width=width,
                    height=height,
                    border=False,
                    no_scrollbar=False,
                )

        self._node_tag = tag

        try:
            self.spec.build(
                content,
                tag_prefix=tag,
                width=width,
                height=height,
            )
        except Exception as exc:
            log.exception(
                "FeSpec.build failed for node=%s member_id=%s: %s",
                self.name,
                self.member_id,
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
                    destroy_hosted_node(tag)
                    self._node_tag = None
                    return
            else:
                destroy_hosted_node(tag)
                self._node_tag = None
                return

        if not dpg.does_item_exist(tag):
            self._node_tag = None
            return

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
            if self._node_tag == tag:
                self._node_tag = None
            if dpg.does_item_exist(tag):
                try:
                    dpg.delete_item(tag)
                except Exception:
                    pass

        if dpg.does_item_exist(content):
            dpg.set_item_user_data(content, _hosted_cleanup)
        dpg.set_item_user_data(tag, _hosted_cleanup)

    def _on_close_clicked(self) -> None:
        """Close button: signal pending delete via user_data flag on the node."""
        tag = self._node_tag or self.hosted_tag()
        if dpg.does_item_exist(tag):
            try:
                dpg.set_item_user_data(f"{tag}::close", True)
            except Exception:
                pass
        self._pending_delete = True

    def consume_pending_delete(self) -> bool:
        pending = self._pending_delete
        self._pending_delete = False
        return pending
