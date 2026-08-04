"""Simple standalone GUI: a single-pane note editor.

Usage:
  python -m new_nodes.quick_note
"""

from __future__ import annotations

from typing import Callable, Optional

import dearpygui.dearpygui as dpg

WIN_W, WIN_H = 360, 280
TAG = "quick_note"
LABEL = "Quick Note"


def build_ui(
    tag: str = TAG,
    *,
    pos: Optional[tuple[float, float]] = None,
    on_close: Optional[Callable[[], None]] = None,
    no_move: bool = False,
    no_resize: bool = True,
    min_size: tuple[int, int] = (220, 160),
) -> str:
    """Build a rectangular note window. Returns the window tag."""
    if dpg.does_item_exist(tag):
        dpg.delete_item(tag)

    kwargs: dict = {}
    if pos is not None:
        kwargs["pos"] = list(pos)

    with dpg.window(
        label=LABEL,
        tag=tag,
        width=WIN_W,
        height=WIN_H,
        no_resize=no_resize,
        min_size=list(min_size),
        no_collapse=True,
        no_move=no_move,
        no_close=on_close is None,
        on_close=lambda: on_close() if on_close else None,
        **kwargs,
    ):
        dpg.add_input_text(
            tag=f"{tag}::title",
            hint="Title",
            width=-1,
            default_value="Untitled",
        )
        dpg.add_separator()
        dpg.add_input_text(
            tag=f"{tag}::body",
            multiline=True,
            height=-40,
            width=-1,
            default_value="Jot an idea…",
        )
        with dpg.group(horizontal=True):
            dpg.add_button(
                label="Clear",
                callback=lambda: _clear(tag),
            )
            dpg.add_button(
                label="Copy title",
                callback=lambda: _echo_title(tag),
            )
            dpg.add_text("", tag=f"{tag}::status", color=(90, 110, 90, 255))
    return tag


def _clear(tag: str) -> None:
    dpg.set_value(f"{tag}::title", "Untitled")
    dpg.set_value(f"{tag}::body", "")
    dpg.set_value(f"{tag}::status", "cleared")


def _echo_title(tag: str) -> None:
    title = dpg.get_value(f"{tag}::title") or "(empty)"
    dpg.set_value(f"{tag}::status", f"“{title}”")


def main() -> None:
    dpg.create_context()
    build_ui()
    dpg.create_viewport(
        title=LABEL,
        width=WIN_W + 16,
        height=WIN_H + 40,
        resizable=False,
    )
    dpg.setup_dearpygui()
    dpg.set_primary_window(TAG, True)
    dpg.show_viewport()
    dpg.start_dearpygui()
    dpg.destroy_context()


if __name__ == "__main__":
    main()
