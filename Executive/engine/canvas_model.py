"""canvas.json load/save and in-memory document model."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from engine.base_node import BaseNode
from engine.registry import create_node, get_node_class


DEFAULT_CANVAS_PATH = Path(__file__).resolve().parent.parent / "canvas.json"


def _new_layer(name: str = "Layer 1") -> dict[str, Any]:
    return {
        "id": str(uuid4()),
        "name": name,
        "visible": True,
        "locked": False,
        "children": [],
    }


def normalize_terms(raw: Any) -> list[dict[str, str]]:
    """Coerce terms from dict, list-of-pairs, or list-of-objects into [{term, definition}]."""
    result: list[dict[str, str]] = []
    if isinstance(raw, dict):
        for key, value in raw.items():
            result.append({"term": str(key), "definition": str(value)})
        return result
    if not isinstance(raw, list):
        return result
    for item in raw:
        if isinstance(item, dict):
            term = item.get("term", item.get("key", ""))
            definition = item.get("definition", item.get("value", ""))
            result.append({"term": str(term), "definition": str(definition)})
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            result.append({"term": str(item[0]), "definition": str(item[1])})
    return result


class CanvasModel:
    """Owns terms / members / hierarchy and keeps node instances in sync."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else DEFAULT_CANVAS_PATH
        self.terms: list[dict[str, str]] = []
        self.members: dict[str, BaseNode] = {}
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

        self.terms = normalize_terms(data.get("terms", []))
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

        for canvas_id, member in iterable:
            type_guid = member.get("type")
            cls = get_node_class(type_guid)
            if cls is None:
                continue
            node = cls.from_member_dict(member)
            self.members[node.canvas_id] = node

        # Rebuild layer membership map from hierarchy
        for layer in self.layers:
            for cid in layer.get("children", []):
                if cid in self.members:
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
            "terms": self.terms,
            "members": {
                cid: node.to_member_dict() for cid, node in self.members.items()
            },
            "hierarchy": {"layers": self.layers},
        }
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    # --- terms ops ---

    def add_term(self, term: str = "", definition: str = "") -> dict[str, str]:
        entry = {"term": term, "definition": definition}
        self.terms.append(entry)
        self.save()
        return entry

    def update_term(self, index: int, term: Optional[str] = None, definition: Optional[str] = None) -> None:
        if index < 0 or index >= len(self.terms):
            return
        if term is not None:
            self.terms[index]["term"] = term
        if definition is not None:
            self.terms[index]["definition"] = definition
        self.save()

    def remove_term(self, index: int) -> None:
        if 0 <= index < len(self.terms):
            self.terms.pop(index)
            self.save()

    def root_member_ids(self, layer_id: Optional[str] = None) -> list[str]:
        """Members with no in-document parent, optionally filtered to a layer."""
        roots: list[str] = []
        for cid, node in self.members.items():
            if layer_id is not None and self._member_layer.get(cid) != layer_id:
                continue
            has_parent = any(pid in self.members for pid in node.parents)
            if not has_parent:
                roots.append(cid)
        return roots

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

    def add_node(
        self,
        type_guid: str,
        position: tuple[float, float],
        layer_id: Optional[str] = None,
        data: Optional[dict[str, Any]] = None,
    ) -> BaseNode:
        node = create_node(type_guid, position=position, data=data)
        node.on_create()
        self.members[node.canvas_id] = node
        target_id = layer_id or self.layers[0]["id"]
        self._member_layer[node.canvas_id] = target_id
        self._relink_containment(node.canvas_id)
        self.save()
        return node

    def delete_node(self, canvas_id: str) -> None:
        node = self.members.get(canvas_id)
        if not node:
            return
        # Unlink from parents/children
        for pid in list(node.parents):
            parent = self.members.get(pid)
            if parent and canvas_id in parent.children:
                parent.children.remove(canvas_id)
                parent.on_object_exit(canvas_id)
        for cid in list(node.children):
            child = self.members.get(cid)
            if child and canvas_id in child.parents:
                child.parents.remove(canvas_id)
        node.on_destroy()
        del self.members[canvas_id]
        self._member_layer.pop(canvas_id, None)
        self.save()

    def move_node(self, canvas_id: str, dx: float, dy: float, move_children: bool = True) -> None:
        node = self.members.get(canvas_id)
        if not node:
            return
        node.on_drag(dx, dy)
        # Only containers drag geometric children.
        if not move_children or not node.is_container:
            return
        stack = list(node.children)
        seen = set(stack)
        while stack:
            cid = stack.pop()
            child = self.members.get(cid)
            if not child:
                continue
            child.move_by(dx, dy)
            for gc in child.children:
                if gc not in seen:
                    seen.add(gc)
                    stack.append(gc)

    def set_parent_child(self, parent_id: str, child_id: str) -> None:
        """Container containment parenting."""
        if parent_id == child_id:
            return
        parent = self.members.get(parent_id)
        child = self.members.get(child_id)
        if not parent or not child:
            return

        # Remove from previous *container* parents only
        for pid in list(child.parents):
            old = self.members.get(pid)
            if old is None:
                child.parents.remove(pid)
                continue
            if not old.is_container:
                continue
            if child_id in old.children:
                old.children.remove(child_id)
                old.on_object_exit(child_id)
            child.parents.remove(pid)

        if child_id not in parent.children:
            parent.children.append(child_id)
        if parent_id not in child.parents:
            child.parents.append(parent_id)
        parent.on_object_enter(child_id)

    def clear_container_parents(self, child_id: str) -> None:
        child = self.members.get(child_id)
        if not child:
            return
        for pid in list(child.parents):
            parent = self.members.get(pid)
            if parent is None:
                child.parents.remove(pid)
                continue
            if not parent.is_container:
                continue
            if child_id in parent.children:
                parent.children.remove(child_id)
                parent.on_object_exit(child_id)
            child.parents.remove(pid)

    def clear_parents(self, child_id: str) -> None:
        """Clear all parents. Prefer clear_container_parents for containment-only."""
        child = self.members.get(child_id)
        if not child:
            return
        for pid in list(child.parents):
            parent = self.members.get(pid)
            if parent and child_id in parent.children:
                parent.children.remove(child_id)
                parent.on_object_exit(child_id)
        child.parents.clear()

    def _node_fully_inside(self, outer: BaseNode, inner: BaseNode) -> bool:
        ox, oy, ow, oh = outer.bounds()
        ix, iy, iw, ih = inner.bounds()
        return ix >= ox and iy >= oy and ix + iw <= ox + ow and iy + ih <= oy + oh

    def _relink_containment(self, moved_id: str) -> None:
        """After a drag ends, update container parent/child links for moved_id."""
        moved = self.members.get(moved_id)
        if not moved:
            return

        # Prefer deepest (smallest area) containing container
        best: BaseNode | None = None
        best_area = float("inf")
        for cid, candidate in self.members.items():
            if cid == moved_id:
                continue
            if candidate.is_container is not True:
                continue
            if not self._node_fully_inside(candidate, moved):
                continue
            _, _, w, h = candidate.bounds()
            area = w * h
            if area < best_area:
                best_area = area
                best = candidate

        if best is not None:
            self.set_parent_child(best.canvas_id, moved_id)
        else:
            self.clear_container_parents(moved_id)

        # If the moved node itself is a container, refresh children that may have left
        if moved.is_container:
            for cid in list(moved.children):
                child = self.members.get(cid)
                if child is None:
                    continue
                if not self._node_fully_inside(moved, child):
                    if moved_id in child.parents:
                        child.parents.remove(moved_id)
                    if cid in moved.children:
                        moved.children.remove(cid)
                        moved.on_object_exit(cid)
            for cid, other in self.members.items():
                if cid == moved_id or cid in moved.children:
                    continue
                if self._node_fully_inside(moved, other):
                    self.set_parent_child(moved_id, cid)
