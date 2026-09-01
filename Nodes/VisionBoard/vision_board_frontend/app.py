"""VisionBoard — a pannable, zoomable sticky-note board hosted as one canvas node.

The board is a single ``drawlist`` redrawn from ``board.Board`` whenever anything
moves, so there is exactly one place that turns world coordinates into pixels.
Mouse handlers are thin: they resolve the cursor to drawlist-local pixels and
hand off to the geometry methods below, which take coordinates as arguments and
therefore run without a mouse.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Optional

from megadesk_contracts import host as dpg

from vision_board_frontend.board import (
    CONTAINER_HEADER,
    CONTAINER_MIN,
    NOTE_PAD,
    NOTE_SIZE,
    Board,
    Camera,
    dump_board,
    fit_text,
    load_board,
)

TOOLBAR_H = 20
GRID_WORLD = 48.0
GRID_MIN_PX = 11.0

COLOR_PAPER = (250, 250, 247, 255)
COLOR_GRID = (228, 230, 234, 255)
COLOR_STICKY = (253, 226, 124, 255)
COLOR_STICKY_EDGE = (212, 178, 58, 255)
COLOR_NOTE_TEXT = (46, 42, 30, 255)
COLOR_FRAME = (18, 18, 22, 255)
COLOR_FRAME_RULE = (150, 152, 158, 255)
COLOR_ACCENT = (44, 110, 210, 255)

_LIVE: dict[str, "VisionBoard"] = {}


class VisionBoard:
    """One board instance: its model, its camera, and the widgets showing them."""

    def __init__(self, parameters: Optional[Mapping[str, str]] = None) -> None:
        self.board = load_board(dict(parameters or {}))
        self.camera = Camera()
        self._root_tag = "vision_board"
        self._w = 400
        self._h = 260
        self._container_mode = False
        self._drag: Optional[dict[str, Any]] = None
        self._press: tuple[float, float] = (0.0, 0.0)
        self._editing: Optional[tuple[str, str]] = None
        self._registry: Optional[str] = None

    def _tag(self, suffix: str) -> str:
        return f"{self._root_tag}::{suffix}"

    # --- geometry entry points (mouse-free, so tests can drive them) ---

    def place_note(self, sx: float, sy: float) -> Optional[str]:
        """Drop a sticky centred on a drawlist point. Ignores an occupied spot."""
        wx, wy = self.camera.to_world(sx, sy)
        if self.board.note_at(wx, wy) is not None:
            return None
        note = self.board.add_note(wx, wy)
        self._redraw()
        return note.id

    def press(self, sx: float, sy: float) -> None:
        """Left press: pick up a sticky, a container, a new frame, or the board."""
        self._end_edit()
        self._press = (sx, sy)
        wx, wy = self.camera.to_world(sx, sy)

        if self._container_mode:
            self._drag = {"kind": "frame", "x0": wx, "y0": wy, "x1": wx, "y1": wy}
            return

        note = self.board.note_at(wx, wy)
        if note is not None:
            self.board.raise_note(note)
            self._drag = {"kind": "note", "id": note.id, "ox": note.x, "oy": note.y}
            self._redraw()
            return

        container = self.board.container_at(wx, wy)
        if container is not None:
            self._drag = {
                "kind": "container",
                "id": container.id,
                "ox": container.x,
                "oy": container.y,
                # Membership is decided once, on pick-up: a sticky the frame is
                # carrying must not be dropped mid-drag when the frame slides
                # off it.
                "carried": [
                    (n.id, n.x, n.y) for n in self.board.notes_in(container)
                ],
            }
            return

        self._drag = {"kind": "pan", "cx": self.camera.x, "cy": self.camera.y}

    def drag(self, dx: float, dy: float) -> None:
        """Continue the active drag. ``dx``/``dy`` are pixels since the press."""
        if self._drag is None:
            return
        kind = self._drag["kind"]
        wdx, wdy = dx / self.camera.zoom, dy / self.camera.zoom

        if kind == "note":
            note = self.board.note(self._drag["id"])
            if note is not None:
                note.x = self._drag["ox"] + wdx
                note.y = self._drag["oy"] + wdy
        elif kind == "container":
            container = self.board.container(self._drag["id"])
            if container is not None:
                container.x = self._drag["ox"] + wdx
                container.y = self._drag["oy"] + wdy
                for note_id, ox, oy in self._drag["carried"]:
                    note = self.board.note(note_id)
                    if note is not None:
                        note.x = ox + wdx
                        note.y = oy + wdy
        elif kind == "pan":
            self.camera.x = self._drag["cx"] - wdx
            self.camera.y = self._drag["cy"] - wdy
        elif kind == "frame":
            self._drag["x1"], self._drag["y1"] = self.camera.to_world(
                self._press[0] + dx, self._press[1] + dy
            )

        self._redraw()

    def release(self) -> Optional[str]:
        """Finish the drag, committing a frame into a container if one was drawn."""
        drag, self._drag = self._drag, None
        created: Optional[str] = None
        if drag is not None and drag["kind"] == "frame":
            if (
                abs(drag["x1"] - drag["x0"]) >= CONTAINER_MIN
                and abs(drag["y1"] - drag["y0"]) >= CONTAINER_MIN
            ):
                created = self.board.add_container(
                    drag["x0"], drag["y0"], drag["x1"], drag["y1"]
                ).id
            self._set_container_mode(False)
        self._redraw()
        return created

    def open_editor(self, sx: float, sy: float) -> Optional[tuple[str, str]]:
        """Double click: type into the sticky, or rename the container header."""
        self._drag = None
        wx, wy = self.camera.to_world(sx, sy)

        note = self.board.note_at(wx, wy)
        if note is not None:
            self._begin_edit("note", note.id, note.text)
            return ("note", note.id)

        container = self.board.container_at(wx, wy)
        if container is not None:
            self._begin_edit("container", container.id, container.name)
            return ("container", container.id)

        self._end_edit()
        return None

    def zoom(self, sx: float, sy: float, steps: float) -> None:
        self.camera.zoom_at(sx, sy, steps)
        self._sync_zoom_label()
        self._redraw()

    def parameters(self) -> dict[str, str]:
        return dump_board(self.board)

    # --- editing ---

    def _begin_edit(self, kind: str, ident: str, text: str) -> None:
        self._editing = (kind, ident)
        if dpg.does_item_exist(self._tag("zoom_lbl")):
            dpg.configure_item(self._tag("zoom_lbl"), show=False)
        edit = self._tag("edit")
        if dpg.does_item_exist(edit):
            dpg.set_value(edit, text)
            dpg.configure_item(edit, show=True)
            dpg.focus_item(edit)
        self._redraw()

    def _end_edit(self) -> None:
        if self._editing is None:
            return
        self._editing = None
        if dpg.does_item_exist(self._tag("edit")):
            dpg.configure_item(self._tag("edit"), show=False)
        if dpg.does_item_exist(self._tag("zoom_lbl")):
            dpg.configure_item(self._tag("zoom_lbl"), show=True)
        self._redraw()

    def _on_edit_changed(self, _sender=None, app_data=None, _user_data=None) -> None:
        if self._editing is None:
            return
        kind, ident = self._editing
        text = str(app_data if app_data is not None else "")
        if kind == "note":
            note = self.board.note(ident)
            if note is not None:
                note.text = text
        else:
            container = self.board.container(ident)
            if container is not None:
                container.name = text
        self._redraw()

    def _set_container_mode(self, on: bool) -> None:
        self._container_mode = on
        tag = self._tag("frame_btn")
        if dpg.does_item_exist(tag):
            dpg.configure_item(tag, label="[]" if on else "[ ]")

    def _on_frame_toggle(self, _sender=None, _app_data=None, _user_data=None) -> None:
        self._set_container_mode(not self._container_mode)

    def _sync_zoom_label(self) -> None:
        tag = self._tag("zoom_lbl")
        if dpg.does_item_exist(tag):
            dpg.set_value(tag, f"{round(self.camera.zoom * 100):d}%")

    # --- drawing ---

    def _redraw(self) -> None:
        board_tag = self._tag("board")
        if not dpg.does_item_exist(board_tag):
            return
        dpg.delete_item(board_tag, children_only=True)
        w, h = float(self._w), float(self._h)
        dpg.draw_rectangle(
            (0, 0), (w, h), parent=board_tag, fill=COLOR_PAPER, color=COLOR_PAPER
        )
        self._draw_grid(board_tag, w, h)

        editing_kind, editing_id = self._editing or ("", "")
        for container in self.board.containers:
            self._draw_container(
                board_tag,
                container,
                w,
                h,
                highlight=editing_kind == "container" and editing_id == container.id,
            )
        for note in self.board.notes:
            self._draw_note(
                board_tag,
                note,
                w,
                h,
                highlight=editing_kind == "note" and editing_id == note.id,
            )

        if self._drag is not None and self._drag["kind"] == "frame":
            a = self.camera.to_screen(self._drag["x0"], self._drag["y0"])
            b = self.camera.to_screen(self._drag["x1"], self._drag["y1"])
            dpg.draw_rectangle(
                a, b, parent=board_tag, color=COLOR_ACCENT, thickness=1.0
            )

    def _draw_grid(self, parent: str, w: float, h: float) -> None:
        step = GRID_WORLD * self.camera.zoom
        if step < GRID_MIN_PX:
            return
        x0 = math.floor(self.camera.x / GRID_WORLD) * GRID_WORLD
        y0 = math.floor(self.camera.y / GRID_WORLD) * GRID_WORLD
        sx, _ = self.camera.to_screen(x0, y0)
        while sx <= w:
            if sx >= 0:
                dpg.draw_line(
                    (sx, 0), (sx, h), parent=parent, color=COLOR_GRID, thickness=1.0
                )
            sx += step
        _, sy = self.camera.to_screen(x0, y0)
        while sy <= h:
            if sy >= 0:
                dpg.draw_line(
                    (0, sy), (w, sy), parent=parent, color=COLOR_GRID, thickness=1.0
                )
            sy += step

    def _draw_container(
        self, parent: str, container: Any, w: float, h: float, *, highlight: bool
    ) -> None:
        x0, y0 = self.camera.to_screen(container.x, container.y)
        x1, y1 = self.camera.to_screen(
            container.x + container.w, container.y + container.h
        )
        if x1 < 0 or y1 < 0 or x0 > w or y0 > h:
            return
        dpg.draw_rectangle(
            (x0, y0),
            (x1, y1),
            parent=parent,
            color=COLOR_ACCENT if highlight else COLOR_FRAME,
            thickness=2.0,
            tag=self._tag(f"container_{container.id}"),
        )
        rule_y = y0 + CONTAINER_HEADER * self.camera.zoom
        if rule_y < y1:
            dpg.draw_line(
                (x0, rule_y),
                (x1, rule_y),
                parent=parent,
                color=COLOR_FRAME_RULE,
                thickness=1.0,
            )
        size = max(8.0, 13.0 * self.camera.zoom)
        if container.name:
            dpg.draw_text(
                (x0 + 5, y0 + max(1.0, (CONTAINER_HEADER * self.camera.zoom - size) / 2)),
                container.name,
                parent=parent,
                size=size,
                color=COLOR_FRAME,
            )

    def _draw_note(
        self, parent: str, note: Any, w: float, h: float, *, highlight: bool
    ) -> None:
        x0, y0 = self.camera.to_screen(note.x, note.y)
        side = NOTE_SIZE * self.camera.zoom
        if x0 + side < 0 or y0 + side < 0 or x0 > w or y0 > h:
            return
        dpg.draw_rectangle(
            (x0, y0),
            (x0 + side, y0 + side),
            parent=parent,
            fill=COLOR_STICKY,
            color=COLOR_ACCENT if highlight else COLOR_STICKY_EDGE,
            thickness=2.0 if highlight else 1.0,
            tag=self._tag(f"note_{note.id}"),
        )
        if not note.text.strip():
            return
        size, lines = fit_text(note.text)
        drawn = size * self.camera.zoom
        if drawn < 5.0:
            return
        pad = NOTE_PAD * self.camera.zoom
        for row, line in enumerate(lines):
            dpg.draw_text(
                (x0 + pad, y0 + pad + row * drawn * 1.15),
                line,
                parent=parent,
                size=drawn,
                color=COLOR_NOTE_TEXT,
            )

    # --- mouse plumbing ---

    def _local(self) -> Optional[tuple[float, float]]:
        """Cursor in drawlist pixels, or ``None`` when it is not over the board."""
        board_tag = self._tag("board")
        if not dpg.does_item_exist(board_tag) or not dpg.is_item_hovered(board_tag):
            return None
        pos = dpg.get_drawing_mouse_pos()
        return (float(pos[0]), float(pos[1]))

    def _on_left_down(self, _sender=None, _app_data=None, _user_data=None) -> None:
        local = self._local()
        if local is None:
            self._end_edit()
            return
        self.press(*local)

    def _on_right_click(self, _sender=None, _app_data=None, _user_data=None) -> None:
        local = self._local()
        if local is not None:
            self.place_note(*local)

    def _on_double_click(self, _sender=None, _app_data=None, _user_data=None) -> None:
        local = self._local()
        if local is not None:
            self.open_editor(*local)

    def _on_drag(self, _sender=None, app_data=None, _user_data=None) -> None:
        if self._drag is None or not app_data:
            return
        self.drag(float(app_data[1]), float(app_data[2]))

    def _on_release(self, _sender=None, _app_data=None, _user_data=None) -> None:
        if self._drag is not None:
            self.release()

    def _on_wheel(self, _sender=None, app_data=None, _user_data=None) -> None:
        local = self._local()
        if local is None or not app_data:
            return
        self.zoom(local[0], local[1], float(app_data))

    # --- lifecycle ---

    def build_ui(
        self,
        parent: str,
        *,
        tag_prefix: str,
        width: int = 440,
        height: int = 320,
    ) -> None:
        self._root_tag = tag_prefix
        self._w = max(160, int(width) - 16)
        self._h = max(120, int(height) - TOOLBAR_H - 12)

        with dpg.group(parent=parent):
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="[ ]",
                    width=26,
                    tag=self._tag("frame_btn"),
                    callback=self._on_frame_toggle,
                )
                dpg.add_text("100%", tag=self._tag("zoom_lbl"))
                dpg.add_input_text(
                    tag=self._tag("edit"),
                    width=-1,
                    show=False,
                    callback=self._on_edit_changed,
                )
            dpg.add_drawlist(
                width=self._w, height=self._h, tag=self._tag("board")
            )

        self._registry = self._tag("handlers")
        dpg.add_handler_registry(tag=self._registry)
        dpg.add_mouse_click_handler(
            button=dpg.mvMouseButton_Left,
            parent=self._registry,
            callback=self._on_left_down,
        )
        dpg.add_mouse_click_handler(
            button=dpg.mvMouseButton_Right,
            parent=self._registry,
            callback=self._on_right_click,
        )
        dpg.add_mouse_double_click_handler(
            button=dpg.mvMouseButton_Left,
            parent=self._registry,
            callback=self._on_double_click,
        )
        dpg.add_mouse_drag_handler(
            button=dpg.mvMouseButton_Left,
            threshold=1.0,
            parent=self._registry,
            callback=self._on_drag,
        )
        dpg.add_mouse_release_handler(
            button=dpg.mvMouseButton_Left,
            parent=self._registry,
            callback=self._on_release,
        )
        dpg.add_mouse_wheel_handler(
            parent=self._registry, callback=self._on_wheel
        )

        dpg.set_item_user_data(parent, self.shutdown)
        _LIVE[tag_prefix] = self
        self._sync_zoom_label()
        self._redraw()

    def shutdown(self) -> None:
        if self._registry and dpg.does_item_exist(self._registry):
            try:
                dpg.delete_item(self._registry)
            except Exception:
                pass
        self._registry = None
        _LIVE.pop(self._root_tag, None)


def build_ui(
    parent: str,
    *,
    tag_prefix: str,
    width: int = 440,
    height: int = 320,
    parameters: Optional[Mapping[str, str]] = None,
) -> None:
    VisionBoard(parameters).build_ui(
        parent, tag_prefix=tag_prefix, width=width, height=height
    )


def read_parameters(tag_prefix: str) -> dict[str, str]:
    """Board layout of the instance hosted under ``tag_prefix``.

    Unlike a text field, a drawlist holds nothing the graph can read back: the
    positions the operator sees *are* the instance's model, so Capture reads it
    directly.
    """
    instance = _LIVE.get(tag_prefix)
    return instance.parameters() if instance is not None else {}
