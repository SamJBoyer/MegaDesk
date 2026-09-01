"""Top bar: pick a graph, save it, name a new one, delete it, capture parameters.

Selecting in the combo loads that graph immediately — the board is what the open
graph says it is. Everything else acts on the open graph. Because any ``.json``
can be selected (the ``…`` button opens a file dialog), every operation reports
:class:`GraphError` into the bar's status text rather than raising into the UI.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from megadesk_contracts import host as dpg

from engine.display_engine import DisplayEngine
from engine.graph_model import GRAPH_SUFFIX, GRAPHS_DIR, GraphError, graph_path_for_name

log = logging.getLogger("megadesk.canvas")

GRAPH_BAR_TAG = "graph_bar"
BAR_HEIGHT = 32

SELECT_TAG = f"{GRAPH_BAR_TAG}::select"
BROWSE_TAG = f"{GRAPH_BAR_TAG}::browse"
DIALOG_TAG = f"{GRAPH_BAR_TAG}::dialog"
SAVE_TAG = f"{GRAPH_BAR_TAG}::save"
NAME_TAG = f"{GRAPH_BAR_TAG}::name"
SAVE_AS_TAG = f"{GRAPH_BAR_TAG}::save_as"
CAPTURE_TAG = f"{GRAPH_BAR_TAG}::capture"
DELETE_TAG = f"{GRAPH_BAR_TAG}::delete"
STATUS_TAG = f"{GRAPH_BAR_TAG}::status"

COLOR_OK = (60, 120, 60, 255)
COLOR_ERROR = (200, 60, 60, 255)
COLOR_DIM = (110, 115, 125, 255)


class GraphBar:
    """The graph chrome above the Catalog and the graph editor."""

    def __init__(self, engine: DisplayEngine) -> None:
        self.engine = engine
        self._by_label: dict[str, Path] = {}

    # --- build ---

    def build(self, parent: str) -> None:
        with dpg.group(horizontal=True, parent=parent):
            dpg.add_text("Graph", color=(40, 45, 55, 255))
            dpg.add_combo(
                tag=SELECT_TAG,
                items=[],
                width=150,
                height_mode=dpg.mvComboHeight_Small,
                callback=self._on_select,
            )
            dpg.add_button(label="…", width=24, tag=BROWSE_TAG, callback=self._on_browse)
            with dpg.tooltip(BROWSE_TAG):
                dpg.add_text("Open any .json as a graph")
            dpg.add_button(label="Save", width=44, tag=SAVE_TAG, callback=self._on_save)
            dpg.add_input_text(
                tag=NAME_TAG, width=96, hint="new name", on_enter=True,
                callback=self._on_save_as,
            )
            dpg.add_button(
                label="Save As", width=60, tag=SAVE_AS_TAG, callback=self._on_save_as
            )
            dpg.add_button(
                label="Capture", width=62, tag=CAPTURE_TAG, callback=self._on_capture
            )
            with dpg.tooltip(CAPTURE_TAG):
                dpg.add_text("Store current sub-GUI values as graph parameters")
            dpg.add_button(
                label="Delete", width=52, tag=DELETE_TAG, callback=self._on_delete
            )
            dpg.add_text("", tag=STATUS_TAG, color=COLOR_DIM)

        with dpg.file_dialog(
            tag=DIALOG_TAG,
            show=False,
            directory_selector=False,
            width=560,
            height=380,
            default_path=str(GRAPHS_DIR if GRAPHS_DIR.is_dir() else Path.cwd()),
            callback=self._on_dialog_pick,
        ):
            dpg.add_file_extension(GRAPH_SUFFIX)

        self.engine.on_graph_changed = self.refresh
        self.refresh()

    # --- state ---

    def refresh(self) -> None:
        """Re-read the graphs directory and mirror which graph is open."""
        if not dpg.does_item_exist(SELECT_TAG):
            return
        self._by_label = {}
        for path in self.engine.graph_choices():
            label = path.stem if path.parent == GRAPHS_DIR else str(path)
            self._by_label[label] = path
        current = self.engine.graph_path()
        current_label = current.stem if current.parent == GRAPHS_DIR else str(current)
        dpg.configure_item(SELECT_TAG, items=sorted(self._by_label))
        dpg.set_value(SELECT_TAG, current_label)

    def _status(self, text: str, color: tuple[int, int, int, int] = COLOR_DIM) -> None:
        if dpg.does_item_exist(STATUS_TAG):
            dpg.set_value(STATUS_TAG, text)
            dpg.configure_item(STATUS_TAG, color=color)

    def _member_count(self) -> int:
        return len(self.engine.model.members)

    # --- callbacks ---

    def _load(self, path: Path) -> None:
        try:
            self.engine.load_graph(path)
        except GraphError as exc:
            self._status(str(exc), COLOR_ERROR)
            self.refresh()
            return
        except Exception as exc:  # noqa: BLE001 - a bad graph must not kill the UI
            log.exception("Loading graph %s failed", path)
            self._status(f"{path.name}: {exc}", COLOR_ERROR)
            self.refresh()
            return
        self._status(f"{self._member_count()} node(s)", COLOR_OK)

    def _on_select(self, sender=None, app_data=None, user_data=None) -> None:
        label = str(app_data or dpg.get_value(SELECT_TAG) or "")
        path = self._by_label.get(label)
        if path is None:
            return
        if path == self.engine.graph_path():
            return
        self._load(path)

    def _on_browse(self, sender=None, app_data=None, user_data=None) -> None:
        dpg.show_item(DIALOG_TAG)

    def _on_dialog_pick(self, sender=None, app_data=None, user_data=None) -> None:
        selected: Optional[str] = None
        if isinstance(app_data, dict):
            selected = app_data.get("file_path_name") or None
            if not selected:
                selections = app_data.get("selections") or {}
                if selections:
                    selected = next(iter(selections.values()))
        if not selected:
            return
        self._load(Path(selected))

    def _on_save(self, sender=None, app_data=None, user_data=None) -> None:
        try:
            self.engine.save_graph()
        except (GraphError, OSError) as exc:
            self._status(str(exc), COLOR_ERROR)
            return
        self._status(f"saved {self._member_count()} node(s)", COLOR_OK)

    def _on_save_as(self, sender=None, app_data=None, user_data=None) -> None:
        name = str(dpg.get_value(NAME_TAG) or "").strip()
        if not name:
            self._status("name a graph first", COLOR_ERROR)
            return
        path = graph_path_for_name(name)
        try:
            self.engine.save_graph_as(path)
        except (GraphError, OSError) as exc:
            self._status(str(exc), COLOR_ERROR)
            return
        dpg.set_value(NAME_TAG, "")
        self._status(f"saved {path.stem}", COLOR_OK)

    def _on_capture(self, sender=None, app_data=None, user_data=None) -> None:
        try:
            captured = self.engine.capture_parameters()
        except (GraphError, OSError) as exc:
            self._status(str(exc), COLOR_ERROR)
            return
        total = sum(len(values) for values in captured.values())
        if not total:
            self._status("no parameters to capture", COLOR_DIM)
            return
        self._status(f"captured {total} parameter(s)", COLOR_OK)

    def _on_delete(self, sender=None, app_data=None, user_data=None) -> None:
        deleted = self.engine.graph_path()
        try:
            self.engine.delete_graph()
        except (GraphError, OSError) as exc:
            self._status(str(exc), COLOR_ERROR)
            return
        self._status(f"deleted {deleted.stem}", COLOR_OK)


def build_graph_bar(engine: DisplayEngine, parent: str) -> GraphBar:
    """Build the bar into ``parent`` and wire it to ``engine``."""
    bar = GraphBar(engine)
    bar.build(parent)
    engine.graph_bar = bar
    return bar
