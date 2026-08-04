"""Sticky note test node — colored square text box."""

from __future__ import annotations

import dearpygui.dearpygui as dpg

from engine.base_node import BaseNode
from engine.registry import register

# Approximate glyph width as a fraction of font size (Dear PyGui default font).
_CHAR_WIDTH_RATIO = 0.55
_LINE_GAP_RATIO = 0.15
_MIN_FONT = 8.0
_MAX_FONT = 18.0
_PAD_X = 10.0
_PAD_Y = 8.0


def _wrap_paragraph(text: str, max_chars: int) -> list[str]:
    """Word-wrap a single paragraph; hard-break overlong tokens."""
    if max_chars < 1:
        max_chars = 1
    if not text:
        return [""]

    lines: list[str] = []
    words = text.split(" ")
    line = ""
    for word in words:
        pieces = [word]
        # Hard-break tokens longer than the line budget.
        if len(word) > max_chars:
            pieces = [
                word[i : i + max_chars] for i in range(0, len(word), max_chars)
            ]
        for piece in pieces:
            trial = (line + " " + piece).strip() if line else piece
            if len(trial) > max_chars and line:
                lines.append(line)
                line = piece
            else:
                line = trial
    if line or not lines:
        lines.append(line)
    return lines


def _layout_text(text: str, max_chars: int) -> list[str]:
    """Preserve explicit newlines, wrap each paragraph to *max_chars*."""
    if not text:
        return [""]
    lines: list[str] = []
    for paragraph in text.split("\n"):
        lines.extend(_wrap_paragraph(paragraph, max_chars))
    return lines or [""]


def _fit_text(
    text: str,
    body_w: float,
    body_h: float,
    preferred: float,
) -> tuple[float, list[str]]:
    """Pick the largest font size whose wrapped lines fit in the body box."""
    preferred = max(_MIN_FONT, min(_MAX_FONT, preferred))
    best_size = _MIN_FONT
    best_lines = _layout_text(text, 1)

    size = preferred
    while size >= _MIN_FONT - 1e-6:
        char_w = max(1.0, size * _CHAR_WIDTH_RATIO)
        max_chars = max(1, int(body_w / char_w))
        lines = _layout_text(text, max_chars)
        line_h = size * (1.0 + _LINE_GAP_RATIO)
        needed = len(lines) * line_h
        if needed <= body_h + 0.5:
            return size, lines
        best_size = size
        best_lines = lines
        size -= 0.5

    # Still overflow at minimum size: clip to what fits.
    char_w = max(1.0, _MIN_FONT * _CHAR_WIDTH_RATIO)
    max_chars = max(1, int(body_w / char_w))
    lines = _layout_text(text, max_chars)
    line_h = _MIN_FONT * (1.0 + _LINE_GAP_RATIO)
    max_lines = max(1, int(body_h / max(line_h, 1e-6)))
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        if lines:
            last = lines[-1]
            if len(last) > 1:
                lines[-1] = last[:-1] + "…"
    return _MIN_FONT, lines


@register
class StickyNode(BaseNode):
    nickname = "Sticky"
    global_guid = "sticky"
    icon = ""
    description = "Colored sticky note. Double-click to edit text."

    has_parent_limit = False
    parent_limit = 0
    has_child_limit = False
    child_limit = 0

    default_width = 160.0
    default_height = 160.0

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.data.setdefault("text", "Sticky")
        self.data.setdefault("fill", [255, 245, 160, 240])
        self.data.setdefault("edge", [70, 70, 75, 255])

    def get_text(self) -> str:
        return str(self.data.get("text", ""))

    def set_text(self, text: str) -> None:
        self.data["text"] = text

    def on_double_click(self) -> None:
        # Display engine opens the modal; hook kept for contract completeness.
        pass

    def draw(self, drawlist, world_to_screen, selected: bool = False) -> None:
        x, y, w, h = self.bounds()
        pmin = world_to_screen(x, y)
        pmax = world_to_screen(x + w, y + h)
        fill = tuple(self.data.get("fill", [255, 245, 160, 240]))
        edge = tuple(self.data.get("edge", [70, 70, 75, 255]))
        thickness = 3 if selected else 1.5
        zoom = self.zoom_safe(world_to_screen)

        dpg.draw_rectangle(
            pmin,
            pmax,
            color=edge,
            fill=fill,
            thickness=thickness,
            rounding=4,
            parent=drawlist,
        )
        # Header strip
        header_h = min(22 * self.scale, h * 0.25)
        header_max = world_to_screen(x + w, y + header_h)
        dpg.draw_rectangle(
            pmin,
            header_max,
            color=edge,
            fill=(edge[0], edge[1], edge[2], 35),
            thickness=0,
            parent=drawlist,
        )
        title_pos = world_to_screen(x + 8, y + 4)
        dpg.draw_text(
            title_pos,
            self.nickname,
            size=max(10, 13 * zoom),
            color=(35, 35, 40, 255),
            parent=drawlist,
        )

        # Body text: wrap inside sticky bounds; shrink font if needed.
        body_x = x + _PAD_X
        body_y = y + header_h + _PAD_Y
        body_w = max(8.0, w - 2 * _PAD_X)
        body_h = max(8.0, h - header_h - 2 * _PAD_Y)
        preferred = 14.0 * zoom
        font_size, lines = _fit_text(self.get_text(), body_w * zoom, body_h * zoom, preferred)
        line_h = font_size * (1.0 + _LINE_GAP_RATIO)
        origin = world_to_screen(body_x, body_y)
        for i, ln in enumerate(lines):
            dpg.draw_text(
                (origin[0], origin[1] + i * line_h),
                ln,
                size=font_size,
                color=(25, 25, 30, 255),
                parent=drawlist,
            )

        # Selection resize handles are drawn by DisplayEngine via BaseNode.

    @staticmethod
    def zoom_safe(world_to_screen) -> float:
        # Infer zoom from unit vector length if closure carries engine zoom
        a = world_to_screen(0, 0)
        b = world_to_screen(1, 0)
        return max(0.2, abs(b[0] - a[0]))
