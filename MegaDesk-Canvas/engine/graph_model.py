"""Graph load/save and the in-memory graph document.

A **graph** is what MegaDesk boots from: which nodes sit on the board, where they
sit, and the parameters each one starts with. Graphs are plain ``.json`` files
and do not have to be named ``graph.json``, so the graph bar can be pointed at
any file — reading one is therefore a validation step (``read_graph_document``)
that raises :class:`GraphError`, never a bare ``json.load``.

Persistence is members-only: ``{"members": {member_id: {...}}}``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Mapping, Optional
from uuid import uuid4

from engine.megadesk_member import TYPE_DISCRIMINATOR, MegaDeskMember
from engine.megadesk_registry import get_fe_spec

log = logging.getLogger("megadesk.canvas")

# engine/ → MegaDesk-Canvas/ → project root that holds Graphs/
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
GRAPHS_DIR = PROJECT_ROOT / "Graphs"
DEFAULT_GRAPH_PATH = GRAPHS_DIR / "default.json"
GRAPH_SUFFIX = ".json"
# Pointer at the last open graph (same idea as Logs/CURRENT). Not a graph file.
CURRENT_FILENAME = "CURRENT"


class GraphError(Exception):
    """The file is not a usable graph. The message is shown in the graph bar."""


def available_graphs(directory: Optional[Path] = None) -> list[Path]:
    """Every ``.json`` file in the graphs directory, alphabetically."""
    root = Path(directory) if directory else GRAPHS_DIR
    try:
        return sorted(p for p in root.glob(f"*{GRAPH_SUFFIX}") if p.is_file())
    except OSError:
        return []


def graph_path_for_name(name: str, directory: Optional[Path] = None) -> Path:
    """Resolve a typed-in graph name to a path inside the graphs directory."""
    root = Path(directory) if directory else GRAPHS_DIR
    stem = Path(str(name).strip()).stem or "graph"
    return root / f"{stem}{GRAPH_SUFFIX}"


def last_graph_pointer_path() -> Path:
    return GRAPHS_DIR / CURRENT_FILENAME


def remember_last_graph(path: Path) -> None:
    """Atomically record ``path`` as the graph to open on the next boot."""
    GRAPHS_DIR.mkdir(parents=True, exist_ok=True)
    pointer = last_graph_pointer_path()
    payload = {"path": str(Path(path).resolve())}
    tmp = GRAPHS_DIR / f"{CURRENT_FILENAME}.tmp"
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(pointer)


def read_last_graph_path() -> Optional[Path]:
    """Return the last-open graph if ``Graphs/CURRENT`` still points at a usable file."""
    pointer = last_graph_pointer_path()
    if not pointer.is_file():
        return None
    try:
        text = pointer.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    raw = str(data.get("path") or "").strip()
    if not raw:
        return None
    candidate = Path(raw)
    if not candidate.is_file():
        return None
    if not is_graph_file(candidate):
        return None
    return candidate


def boot_graph_path() -> Path:
    """Graph to open at canvas start: last open if still valid, else ``default.json``."""
    return read_last_graph_path() or DEFAULT_GRAPH_PATH


def read_graph_document(path: Any) -> dict[str, dict[str, Any]]:
    """Validate ``path`` as a graph and return its member records by id.

    Raises :class:`GraphError` with a message worth showing an operator who just
    picked the wrong ``.json``.
    """
    file_path = Path(path)
    label = file_path.name

    try:
        text = file_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise GraphError(f"{label}: no such file") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise GraphError(f"{label}: cannot read ({exc})") from exc

    try:
        document = json.loads(text or "{}")
    except json.JSONDecodeError as exc:
        raise GraphError(f"{label}: not valid JSON (line {exc.lineno})") from exc

    if not isinstance(document, dict):
        raise GraphError(f"{label}: not a graph (top level is not an object)")
    if "members" not in document:
        raise GraphError(f"{label}: not a graph (no 'members')")

    raw_members = document["members"]
    if isinstance(raw_members, dict):
        entries = list(raw_members.items())
    elif isinstance(raw_members, list):
        entries = [(None, member) for member in raw_members]
    else:
        raise GraphError(f"{label}: not a graph ('members' is not an object)")

    records: dict[str, dict[str, Any]] = {}
    for key, member in entries:
        if not isinstance(member, dict):
            raise GraphError(f"{label}: member {key or '?'} is not an object")
        member_type = member.get("type", TYPE_DISCRIMINATOR)
        if member_type != TYPE_DISCRIMINATOR:
            raise GraphError(f"{label}: unknown member type {member_type!r}")
        node_name = member.get("node_name") or (member.get("data") or {}).get("node_name")
        if not node_name:
            raise GraphError(f"{label}: member {key or '?'} has no node_name")
        member_id = str(member.get("member_id") or key or uuid4())
        record = dict(member)
        record["member_id"] = member_id
        record["node_name"] = str(node_name)
        records[member_id] = record
    return records


def is_graph_file(path: Any) -> bool:
    """Whether ``path`` reads as a graph. For filtering, not for reporting."""
    try:
        read_graph_document(path)
    except GraphError:
        return False
    return True


class GraphModel:
    """Owns the graph's members and persists them to its ``.json`` file."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else boot_graph_path()
        self.members: dict[str, MegaDeskMember] = {}

    # --- persistence ---

    def load(self) -> None:
        """Populate members from ``self.path``, creating an empty graph if absent."""
        if not self.path.exists():
            self.save()
            return
        self._populate(read_graph_document(self.path))

    def load_from(self, path: Any) -> None:
        """Switch to another graph file.

        The new file is read and validated **before** the current board is torn
        down, so pointing the bar at a random ``.json`` leaves the open graph
        untouched.
        """
        candidate = Path(path)
        records = read_graph_document(candidate)
        self.close_all()
        self.path = candidate
        self._populate(records)

    def _populate(self, records: Mapping[str, dict[str, Any]]) -> None:
        self.members.clear()
        for member_id, record in records.items():
            node_name = str(record.get("node_name"))
            spec = get_fe_spec(node_name, parameters=record.get("parameters"))
            if spec is None:
                log.warning(
                    "Graph %s references node %r, which is not installed — skipping",
                    self.path.name,
                    node_name,
                )
                continue
            member = MegaDeskMember.from_member_dict(record, spec)
            self.members[member.member_id] = member

    def save(self) -> None:
        payload = {
            "members": {
                member_id: member.to_member_dict()
                for member_id, member in self.members.items()
            },
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    def save_as(self, path: Any) -> None:
        self.path = Path(path)
        self.save()

    def delete_file(self) -> None:
        """Remove this graph's file from disk; the board itself is untouched."""
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise GraphError(f"{self.path.name}: cannot delete ({exc})") from exc

    # --- member ops ---

    def add_megadesk_node(
        self,
        name: str,
        position: tuple[float, float],
        data: Optional[dict[str, Any]] = None,
        parameters: Optional[Mapping[str, str]] = None,
    ) -> MegaDeskMember:
        spec = get_fe_spec(name, parameters=parameters)
        if spec is None:
            raise KeyError(f"Unknown MegaDesk FE node: {name}")
        member = MegaDeskMember(
            spec, position=position, data=data, parameters=parameters
        )
        self.members[member.member_id] = member
        self.save()
        return member

    def delete_node(self, member_id: str) -> None:
        member = self.members.get(member_id)
        if not member:
            return
        member.destroy_node()
        del self.members[member_id]
        self.save()

    def close_all(self) -> None:
        """Tear every hosted FE down without touching the file on disk."""
        for member in list(self.members.values()):
            member.destroy_node()
        self.members.clear()

    def move_node(self, member_id: str, dx: float, dy: float) -> None:
        member = self.members.get(member_id)
        if not member:
            return
        member.position[0] += float(dx)
        member.position[1] += float(dy)

    # --- parameters ---

    def capture_parameters(self) -> dict[str, dict[str, str]]:
        """Pull current values out of every hosted FE into its member record.

        Returns the captured values per member id, for members that reported any.
        """
        captured: dict[str, dict[str, str]] = {}
        for member_id, member in self.members.items():
            values = member.read_live_parameters()
            if not values:
                continue
            captured[member_id] = member.set_parameters(values)
        return captured
