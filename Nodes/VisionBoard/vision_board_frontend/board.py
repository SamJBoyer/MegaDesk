"""The board itself: stickies, containers, camera, and the text that fits a note.

Kept free of Dear PyGui so the geometry that decides what the operator sees —
which sticky a click landed on, which stickies a container carries, what font
size the note text collapses to — can be exercised without a desktop session.
``app.py`` owns every pixel; this module owns world coordinates.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional
from uuid import uuid4

# World units. The camera scales these to pixels; nothing here knows about zoom.
NOTE_SIZE = 96.0
NOTE_PAD = 7.0
CONTAINER_MIN = 48.0
CONTAINER_HEADER = 20.0
# How far outside-in a container border still counts as a grab: the inside is
# transparent so that clicks reach the stickies it holds.
CONTAINER_GRAB = 9.0

# Advance width of Dear PyGui's built-in font as a fraction of its size.
CHAR_ASPECT = 0.5
LINE_SPACING = 1.15
FONT_MAX = 17.0
FONT_MIN = 7.0


def new_id() -> str:
    return uuid4().hex[:8]


@dataclass
class Note:
    """A sticky. Square, positioned by its top-left corner in world space."""

    id: str = field(default_factory=new_id)
    x: float = 0.0
    y: float = 0.0
    text: str = ""

    def contains(self, x: float, y: float) -> bool:
        return self.x <= x <= self.x + NOTE_SIZE and self.y <= y <= self.y + NOTE_SIZE

    def center(self) -> tuple[float, float]:
        return (self.x + NOTE_SIZE / 2, self.y + NOTE_SIZE / 2)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "x": round(self.x, 2),
            "y": round(self.y, 2),
            "text": self.text,
        }


@dataclass
class Container:
    """A named frame drawn around stickies. Border only; the inside is empty."""

    id: str = field(default_factory=new_id)
    x: float = 0.0
    y: float = 0.0
    w: float = CONTAINER_MIN
    h: float = CONTAINER_MIN
    name: str = ""

    def header_contains(self, x: float, y: float) -> bool:
        return (
            self.x <= x <= self.x + self.w
            and self.y <= y <= self.y + CONTAINER_HEADER
        )

    def border_contains(self, x: float, y: float) -> bool:
        """True on the frame itself, not in the hollow middle."""
        outer = (
            self.x - CONTAINER_GRAB <= x <= self.x + self.w + CONTAINER_GRAB
            and self.y - CONTAINER_GRAB <= y <= self.y + self.h + CONTAINER_GRAB
        )
        if not outer:
            return False
        inner = (
            self.x + CONTAINER_GRAB < x < self.x + self.w - CONTAINER_GRAB
            and self.y + CONTAINER_GRAB < y < self.y + self.h - CONTAINER_GRAB
        )
        return not inner

    def holds(self, note: Note) -> bool:
        cx, cy = note.center()
        return self.x <= cx <= self.x + self.w and self.y <= cy <= self.y + self.h

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "x": round(self.x, 2),
            "y": round(self.y, 2),
            "w": round(self.w, 2),
            "h": round(self.h, 2),
            "name": self.name,
        }


class Board:
    """Every sticky and container, in paint order (last drawn is on top)."""

    def __init__(
        self,
        notes: Optional[Iterable[Note]] = None,
        containers: Optional[Iterable[Container]] = None,
    ) -> None:
        self.notes: list[Note] = list(notes or ())
        self.containers: list[Container] = list(containers or ())

    def add_note(self, cx: float, cy: float, text: str = "") -> Note:
        """A sticky centered on a world point — where the operator clicked."""
        note = Note(x=cx - NOTE_SIZE / 2, y=cy - NOTE_SIZE / 2, text=text)
        self.notes.append(note)
        return note

    def add_container(
        self, x0: float, y0: float, x1: float, y1: float, name: str = ""
    ) -> Container:
        x, y = min(x0, x1), min(y0, y1)
        container = Container(
            x=x,
            y=y,
            w=max(CONTAINER_MIN, abs(x1 - x0)),
            h=max(CONTAINER_MIN, abs(y1 - y0)),
            name=name,
        )
        self.containers.append(container)
        return container

    def note(self, note_id: str) -> Optional[Note]:
        return next((n for n in self.notes if n.id == note_id), None)

    def container(self, container_id: str) -> Optional[Container]:
        return next((c for c in self.containers if c.id == container_id), None)

    def note_at(self, x: float, y: float) -> Optional[Note]:
        for note in reversed(self.notes):
            if note.contains(x, y):
                return note
        return None

    def container_at(self, x: float, y: float) -> Optional[Container]:
        """The frame under a point, grabbed by its header or its border."""
        for container in reversed(self.containers):
            if container.header_contains(x, y) or container.border_contains(x, y):
                return container
        return None

    def notes_in(self, container: Container) -> list[Note]:
        return [n for n in self.notes if container.holds(n)]

    def raise_note(self, note: Note) -> None:
        """Bring a sticky to the front so a drag is drawn over its neighbours."""
        if note in self.notes:
            self.notes.remove(note)
            self.notes.append(note)


# --- text ------------------------------------------------------------------


def wrap_lines(text: str, max_chars: int) -> list[str]:
    """Greedy word wrap at ``max_chars``, hard-splitting words too long to fit."""
    if max_chars < 1:
        max_chars = 1
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = ""
        for word in words:
            while len(word) > max_chars:
                if current:
                    lines.append(current)
                    current = ""
                lines.append(word[:max_chars])
                word = word[max_chars:]
            if not current:
                current = word
            elif len(current) + 1 + len(word) <= max_chars:
                current = f"{current} {word}"
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def fit_text(
    text: str,
    box_w: float = NOTE_SIZE,
    box_h: float = NOTE_SIZE,
    *,
    pad: float = NOTE_PAD,
) -> tuple[float, list[str]]:
    """Largest font size at which ``text`` wraps inside the box, and its lines.

    Steps down from ``FONT_MAX`` and stops at the first size that fits. At
    ``FONT_MIN`` the note is full: the overflow is dropped rather than drawn
    past the sticky's edge.
    """
    inner_w = max(1.0, box_w - 2 * pad)
    inner_h = max(1.0, box_h - 2 * pad)
    if not text.strip():
        return FONT_MAX, []

    size = FONT_MAX
    lines: list[str] = []
    while size >= FONT_MIN:
        lines = wrap_lines(text, int(inner_w / (size * CHAR_ASPECT)))
        if len(lines) * size * LINE_SPACING <= inner_h:
            return size, lines
        size -= 1.0

    size = FONT_MIN
    lines = wrap_lines(text, int(inner_w / (size * CHAR_ASPECT)))
    room = max(1, int(inner_h / (size * LINE_SPACING)))
    return size, lines[:room]


# --- camera ----------------------------------------------------------------

ZOOM_MIN = 0.3
ZOOM_MAX = 3.0
ZOOM_STEP = 1.12


@dataclass
class Camera:
    """Where the viewport sits over the board. ``x``/``y`` is its top-left."""

    x: float = 0.0
    y: float = 0.0
    zoom: float = 1.0

    def to_screen(self, wx: float, wy: float) -> tuple[float, float]:
        return ((wx - self.x) * self.zoom, (wy - self.y) * self.zoom)

    def to_world(self, sx: float, sy: float) -> tuple[float, float]:
        return (sx / self.zoom + self.x, sy / self.zoom + self.y)

    def pan(self, dx_screen: float, dy_screen: float) -> None:
        """Drag the board with the cursor, so ``dx`` moves content, not the eye."""
        self.x -= dx_screen / self.zoom
        self.y -= dy_screen / self.zoom

    def zoom_at(self, sx: float, sy: float, steps: float) -> None:
        """Scale about a screen point, keeping the world under it pinned."""
        target = max(ZOOM_MIN, min(ZOOM_MAX, self.zoom * (ZOOM_STEP**steps)))
        if target == self.zoom:
            return
        wx, wy = self.to_world(sx, sy)
        self.zoom = target
        self.x = wx - sx / target
        self.y = wy - sy / target


# --- graph parameters ------------------------------------------------------

PARAM_NOTES = "NOTES"
PARAM_CONTAINERS = "CONTAINERS"


def _as_float(value: Any, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _entries(raw: str) -> list[dict[str, Any]]:
    """Decode a parameter value, tolerating anything a hand-edited graph holds."""
    text = (raw or "").strip()
    if not text:
        return []
    try:
        decoded = json.loads(text)
    except (TypeError, ValueError):
        return []
    if not isinstance(decoded, list):
        return []
    return [item for item in decoded if isinstance(item, dict)]


def load_notes(raw: str) -> list[Note]:
    return [
        Note(
            id=str(item.get("id") or new_id()),
            x=_as_float(item.get("x")),
            y=_as_float(item.get("y")),
            text=str(item.get("text") or ""),
        )
        for item in _entries(raw)
    ]


def load_containers(raw: str) -> list[Container]:
    out: list[Container] = []
    for item in _entries(raw):
        out.append(
            Container(
                id=str(item.get("id") or new_id()),
                x=_as_float(item.get("x")),
                y=_as_float(item.get("y")),
                w=max(CONTAINER_MIN, _as_float(item.get("w"), CONTAINER_MIN)),
                h=max(CONTAINER_MIN, _as_float(item.get("h"), CONTAINER_MIN)),
                name=str(item.get("name") or ""),
            )
        )
    return out


def dump_board(board: Board) -> dict[str, str]:
    """The board as the two graph parameters that carry it between sessions."""
    return {
        PARAM_NOTES: json.dumps([n.as_dict() for n in board.notes]),
        PARAM_CONTAINERS: json.dumps([c.as_dict() for c in board.containers]),
    }


def load_board(parameters: Optional[dict[str, str]] = None) -> Board:
    values = parameters or {}
    return Board(
        notes=load_notes(values.get(PARAM_NOTES, "")),
        containers=load_containers(values.get(PARAM_CONTAINERS, "")),
    )
