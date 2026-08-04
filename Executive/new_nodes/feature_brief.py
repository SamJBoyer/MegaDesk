"""Medium standalone GUI: ideation brief form with sections and preview.

Usage:
  python -m new_nodes.feature_brief
"""

from __future__ import annotations

from typing import Any, Callable, Optional

import dearpygui.dearpygui as dpg

WIN_W, WIN_H = 520, 560
TAG = "feature_brief"
LABEL = "Feature Brief"

_PRIORITIES = ("Low", "Medium", "High", "Critical")
_STACKS = ("Python", "TypeScript", "Rust", "Go", "Other")


def build_ui(
    tag: str = TAG,
    *,
    pos: Optional[tuple[float, float]] = None,
    on_close: Optional[Callable[[], None]] = None,
    no_move: bool = False,
    no_resize: bool = True,
    min_size: tuple[int, int] = (360, 320),
) -> str:
    """Build a rectangular feature-brief form. Returns the window tag."""
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
        dpg.add_text("Capture a feature idea for later PDR / ticket export.")
        dpg.add_separator()

        with dpg.child_window(tag=f"{tag}::form", width=-1, height=-120, border=True):
            dpg.add_text("Basics")
            dpg.add_input_text(
                tag=f"{tag}::name",
                label="Name",
                width=-120,
                default_value="Canvas export",
            )
            dpg.add_combo(
                items=list(_PRIORITIES),
                tag=f"{tag}::priority",
                label="Priority",
                default_value="Medium",
                width=-120,
            )
            dpg.add_combo(
                items=list(_STACKS),
                tag=f"{tag}::stack",
                label="Stack",
                default_value="Python",
                width=-120,
            )
            dpg.add_separator()

            dpg.add_text("Scope")
            dpg.add_input_text(
                tag=f"{tag}::summary",
                label="Summary",
                multiline=True,
                height=72,
                width=-120,
                default_value="Export a selected group as structured metadata.",
            )
            dpg.add_input_text(
                tag=f"{tag}::acceptance",
                label="Acceptance",
                multiline=True,
                height=72,
                width=-120,
                default_value="- Group id preserved\n- Children listed\n- Clipboard + file",
            )
            dpg.add_separator()

            dpg.add_text("Flags")
            with dpg.group(horizontal=True):
                dpg.add_checkbox(tag=f"{tag}::needs_ui", label="Needs UI", default_value=True)
                dpg.add_checkbox(tag=f"{tag}::needs_api", label="Needs API")
                dpg.add_checkbox(tag=f"{tag}::blocked", label="Blocked")

            dpg.add_input_text(
                tag=f"{tag}::tags",
                label="Tags",
                width=-120,
                hint="comma,separated",
                default_value="canvas,metadata,export",
            )

        dpg.add_separator()
        dpg.add_text("Preview", color=(70, 80, 100, 255))
        dpg.add_input_text(
            tag=f"{tag}::preview",
            multiline=True,
            readonly=True,
            height=70,
            width=-1,
            default_value="",
        )
        with dpg.group(horizontal=True):
            dpg.add_button(
                label="Refresh preview",
                callback=lambda: _refresh_preview(tag),
            )
            dpg.add_button(
                label="Reset form",
                callback=lambda: _reset_form(tag),
            )
            dpg.add_text("", tag=f"{tag}::status", color=(90, 110, 90, 255))

    _refresh_preview(tag)
    return tag


def _collect(tag: str) -> dict[str, Any]:
    return {
        "name": dpg.get_value(f"{tag}::name"),
        "priority": dpg.get_value(f"{tag}::priority"),
        "stack": dpg.get_value(f"{tag}::stack"),
        "summary": dpg.get_value(f"{tag}::summary"),
        "acceptance": dpg.get_value(f"{tag}::acceptance"),
        "needs_ui": bool(dpg.get_value(f"{tag}::needs_ui")),
        "needs_api": bool(dpg.get_value(f"{tag}::needs_api")),
        "blocked": bool(dpg.get_value(f"{tag}::blocked")),
        "tags": [
            t.strip()
            for t in str(dpg.get_value(f"{tag}::tags") or "").split(",")
            if t.strip()
        ],
    }


def _refresh_preview(tag: str) -> None:
    data = _collect(tag)
    flags = []
    if data["needs_ui"]:
        flags.append("ui")
    if data["needs_api"]:
        flags.append("api")
    if data["blocked"]:
        flags.append("blocked")
    lines = [
        f"# {data['name']}  [{data['priority']} · {data['stack']}]",
        f"tags: {', '.join(data['tags']) or '—'}",
        f"flags: {', '.join(flags) or '—'}",
        "",
        str(data["summary"] or "").strip(),
    ]
    dpg.set_value(f"{tag}::preview", "\n".join(lines))
    dpg.set_value(f"{tag}::status", "preview updated")


def _reset_form(tag: str) -> None:
    dpg.set_value(f"{tag}::name", "")
    dpg.set_value(f"{tag}::priority", "Medium")
    dpg.set_value(f"{tag}::stack", "Python")
    dpg.set_value(f"{tag}::summary", "")
    dpg.set_value(f"{tag}::acceptance", "")
    dpg.set_value(f"{tag}::needs_ui", False)
    dpg.set_value(f"{tag}::needs_api", False)
    dpg.set_value(f"{tag}::blocked", False)
    dpg.set_value(f"{tag}::tags", "")
    _refresh_preview(tag)
    dpg.set_value(f"{tag}::status", "reset")


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
