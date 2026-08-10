"""canvas.json load/save and in-memory document model."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from engine.megadesk_member import TYPE_DISCRIMINATOR, MegaDeskMember
from engine.megadesk_registry import get_fe_spec

CanvasMember = MegaDeskMember

# engine/ → MegaDesk-Canvas/ → project root where canvas.json lives
DEFAULT_CANVAS_PATH = Path(__file__).resolve().parent.parent.parent / "canvas.json"


class CanvasModel:
    """Owns MegaDesk members and persists them to canvas.json.

    Persistence is members-only: ``{"members": {...}}``. Legacy ``hierarchy``
    keys in older canvas.json files are ignored on load and never written back.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else DEFAULT_CANVAS_PATH
        self.members: dict[str, CanvasMember] = {}

    # --- persistence ---

    def load(self) -> None:
        if not self.path.exists():
            self.save()
            return

        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.members.clear()

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

    def save(self) -> None:
        payload = {
            "members": {
                cid: node.to_member_dict() for cid, node in self.members.items()
            },
        }
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    # --- member ops ---

    def add_megadesk_node(
        self,
        name: str,
        position: tuple[float, float],
        data: Optional[dict[str, Any]] = None,
    ) -> MegaDeskMember:
        spec = get_fe_spec(name)
        if spec is None:
            raise KeyError(f"Unknown MegaDesk FE node: {name}")
        node = MegaDeskMember(spec, position=position, data=data)
        node.on_create()
        self.members[node.canvas_id] = node
        self.save()
        return node

    def delete_node(self, canvas_id: str) -> None:
        node = self.members.get(canvas_id)
        if not node:
            return
        node.on_destroy()
        del self.members[canvas_id]
        self.save()

    def move_node(self, canvas_id: str, dx: float, dy: float) -> None:
        node = self.members.get(canvas_id)
        if not node:
            return
        node.position[0] += float(dx)
        node.position[1] += float(dy)
