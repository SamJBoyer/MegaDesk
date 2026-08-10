"""canvas.json load/save and in-memory document model."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from engine.megadesk_member import TYPE_DISCRIMINATOR, MegaDeskMember
from engine.megadesk_registry import get_fe_spec

CanvasMember = MegaDeskMember

# engine/ → MegaDesk-Canvas/ → project root where canvas.json lives
DEFAULT_CANVAS_PATH = Path(__file__).resolve().parent.parent.parent / "canvas.json"


def _new_layer(name: str = "Layer 1") -> dict[str, Any]:
    return {
        "id": str(uuid4()),
        "name": name,
        "visible": True,
        "locked": False,
        "children": [],
    }


class CanvasModel:
    """Owns members / hierarchy.layers and keeps MegaDesk instances in sync."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else DEFAULT_CANVAS_PATH
        self.members: dict[str, CanvasMember] = {}
        self.layers: list[dict[str, Any]] = []
        self._member_layer: dict[str, str] = {}  # canvas_id -> layer_id

    # --- persistence ---

    def load(self) -> None:
        if not self.path.exists():
            self.layers = [_new_layer("Layer 1")]
            self.save()
            return

        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.layers = list(data.get("hierarchy", {}).get("layers", []))
        if not self.layers:
            self.layers = [_new_layer("Layer 1")]

        self.members.clear()
        self._member_layer.clear()

        raw_members = data.get("members", {})
        if isinstance(raw_members, list):
            iterable = ((m.get("canvas_id"), m) for m in raw_members)
        else:
            iterable = raw_members.items()

        for _canvas_id, member in iterable:
            type_guid = member.get("type")
            if type_guid != TYPE_DISCRIMINATOR:
                continue
            node_name = member.get("node_name") or (member.get("data") or {}).get(
                "node_name"
            )
            if not node_name:
                continue
            spec = get_fe_spec(str(node_name))
            if spec is None:
                continue
            node = MegaDeskMember.from_member_dict(member, spec)
            self.members[node.canvas_id] = node

        # Rebuild layer membership map from hierarchy; drop missing children
        for layer in self.layers:
            kept = [cid for cid in layer.get("children", []) if cid in self.members]
            layer["children"] = kept
            for cid in kept:
                self._member_layer[cid] = layer["id"]

        # Orphans go onto the first layer
        for cid in self.members:
            if cid not in self._member_layer:
                self.layers[0].setdefault("children", []).append(cid)
                self._member_layer[cid] = self.layers[0]["id"]

    def save(self) -> None:
        # Sync hierarchy children lists from _member_layer
        for layer in self.layers:
            layer["children"] = [
                cid
                for cid, lid in self._member_layer.items()
                if lid == layer["id"] and cid in self.members
            ]

        payload = {
            "members": {
                cid: node.to_member_dict() for cid, node in self.members.items()
            },
            "hierarchy": {"layers": self.layers},
        }
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    # --- layer ops ---

    def active_layer(self) -> dict[str, Any]:
        return self.layers[0] if self.layers else _new_layer()

    def get_layer(self, layer_id: str) -> dict[str, Any] | None:
        for layer in self.layers:
            if layer["id"] == layer_id:
                return layer
        return None

    def create_layer(self, name: Optional[str] = None) -> dict[str, Any]:
        layer = _new_layer(name or f"Layer {len(self.layers) + 1}")
        self.layers.append(layer)
        self.save()
        return layer

    def rename_layer(self, layer_id: str, name: str) -> None:
        layer = self.get_layer(layer_id)
        if layer:
            layer["name"] = name
            self.save()

    def remove_layer(self, layer_id: str) -> None:
        if len(self.layers) <= 1:
            return
        layer = self.get_layer(layer_id)
        if not layer:
            return
        # Move remaining objects to first remaining layer
        target = next(l for l in self.layers if l["id"] != layer_id)
        for cid in list(layer.get("children", [])):
            self._member_layer[cid] = target["id"]
        self.layers = [l for l in self.layers if l["id"] != layer_id]
        self.save()

    def set_layer_visible(self, layer_id: str, visible: bool) -> None:
        layer = self.get_layer(layer_id)
        if layer:
            layer["visible"] = bool(visible)
            self.save()

    def set_layer_locked(self, layer_id: str, locked: bool) -> None:
        layer = self.get_layer(layer_id)
        if layer:
            layer["locked"] = bool(locked)
            self.save()

    def layer_for(self, canvas_id: str) -> dict[str, Any] | None:
        lid = self._member_layer.get(canvas_id)
        return self.get_layer(lid) if lid else None

    def is_locked(self, canvas_id: str) -> bool:
        layer = self.layer_for(canvas_id)
        return bool(layer and layer.get("locked"))

    def is_visible(self, canvas_id: str) -> bool:
        layer = self.layer_for(canvas_id)
        return bool(layer and layer.get("visible", True))

    # --- member ops ---

    def add_megadesk_node(
        self,
        name: str,
        position: tuple[float, float],
        layer_id: Optional[str] = None,
        data: Optional[dict[str, Any]] = None,
    ) -> MegaDeskMember:
        spec = get_fe_spec(name)
        if spec is None:
            raise KeyError(f"Unknown MegaDesk FE node: {name}")
        node = MegaDeskMember(spec, position=position, data=data)
        node.on_create()
        self.members[node.canvas_id] = node
        target_id = layer_id or self.layers[0]["id"]
        self._member_layer[node.canvas_id] = target_id
        self.save()
        return node

    def delete_node(self, canvas_id: str) -> None:
        node = self.members.get(canvas_id)
        if not node:
            return
        node.on_destroy()
        del self.members[canvas_id]
        self._member_layer.pop(canvas_id, None)
        self.save()

    def move_node(self, canvas_id: str, dx: float, dy: float) -> None:
        node = self.members.get(canvas_id)
        if not node:
            return
        node.on_drag(dx, dy)
