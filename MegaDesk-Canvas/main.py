"""MegaDesk canvas — Dear PyGui node_editor hosting a graph of MegaDesk FE nodes."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import dearpygui.dearpygui as dpg
from megadesk_contracts import ENV_CANVAS_ROOT, ensure_supervisor_running, frame_pump
from megadesk_contracts.log_session import attach_log_session, session_log_path

from engine.display_engine import (
    GRAPH_WINDOW,
    NODE_EDITOR,
    PAYLOAD_TYPE,
    REF_NODE,
    SIDEBAR_TAG,
    DisplayEngine,
)
from engine.graph_bar import BAR_HEIGHT, GRAPH_BAR_TAG, build_graph_bar
from engine.graph_model import GraphError, GraphModel
from engine.megadesk_registry import discover_megadesk_frontends
from supervisor.panel import build_supervisor_panel, reposition_supervisor_panel


def _apply_daytime_theme() -> None:
    """White / light-gray daytime chrome for panels and widgets."""
    with dpg.theme() as theme:
        with dpg.theme_component(dpg.mvAll):
            dpg.add_theme_color(dpg.mvThemeCol_WindowBg, (245, 247, 250, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (250, 251, 253, 255))
            dpg.add_theme_color(dpg.mvThemeCol_PopupBg, (255, 255, 255, 250))
            dpg.add_theme_color(dpg.mvThemeCol_Border, (200, 205, 215, 255))
            dpg.add_theme_color(dpg.mvThemeCol_Text, (30, 32, 38, 255))
            dpg.add_theme_color(dpg.mvThemeCol_TextDisabled, (130, 135, 145, 255))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBg, (255, 255, 255, 255))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, (235, 240, 248, 255))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgActive, (220, 228, 240, 255))
            dpg.add_theme_color(dpg.mvThemeCol_TitleBg, (230, 234, 240, 255))
            dpg.add_theme_color(dpg.mvThemeCol_TitleBgActive, (220, 226, 235, 255))
            dpg.add_theme_color(dpg.mvThemeCol_Button, (235, 238, 244, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (220, 228, 240, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (200, 214, 235, 255))
            dpg.add_theme_color(dpg.mvThemeCol_Header, (220, 228, 240, 255))
            dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered, (210, 220, 235, 255))
            dpg.add_theme_color(dpg.mvThemeCol_HeaderActive, (190, 205, 230, 255))
            dpg.add_theme_color(dpg.mvThemeCol_CheckMark, (50, 110, 200, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarBg, (240, 242, 246, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrab, (190, 195, 205, 255))
            dpg.add_theme_color(dpg.mvThemeCol_Separator, (200, 205, 215, 255))
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 3)
            dpg.add_theme_style(dpg.mvStyleVar_WindowRounding, 4)
    dpg.bind_theme(theme)


def build_canvas(
    model: GraphModel,
    *,
    width: int = 1280,
    height: int = 800,
    viewport_pos: tuple[int, int] | None = None,
    supervisor_panel: bool = True,
    graph_bar: bool = True,
) -> DisplayEngine:
    """Construct the canvas UI for ``model`` and return its engine.

    Everything from ``create_context()`` up to (but excluding) the render loop.
    Callers own discovery, ``model.load()``, the loop, and ``destroy_context()``.
    ``viewport_pos`` places the viewport off-screen for harnessed runs — it must
    still be shown, since a minimized viewport renders nothing.
    """
    engine = DisplayEngine(model)

    dpg.create_context()
    _apply_daytime_theme()

    with dpg.handler_registry():
        dpg.add_key_press_handler(callback=engine.on_key_press)

    with dpg.theme() as ref_theme:
        with dpg.theme_component(dpg.mvNode):
            dpg.add_theme_style(
                dpg.mvNodeStyleVar_NodePadding, 0, 0, category=dpg.mvThemeCat_Nodes
            )

    with dpg.window(
        label="Graph",
        tag=GRAPH_WINDOW,
        no_title_bar=True,
        no_move=True,
        no_resize=True,
        no_bring_to_front_on_focus=True,
        no_scrollbar=True,
        no_scroll_with_mouse=True,
        pos=(0, 0),
        width=width,
        height=height,
    ):
        if graph_bar:
            dpg.add_child_window(
                tag=GRAPH_BAR_TAG,
                height=BAR_HEIGHT,
                border=True,
                no_scrollbar=True,
            )
        with dpg.group(horizontal=True):
            dpg.add_child_window(
                tag=SIDEBAR_TAG,
                width=240,
                border=True,
                no_scrollbar=False,
            )
            with dpg.child_window(width=-1, border=True, no_scrollbar=True):
                with dpg.group(
                    drop_callback=engine.on_graph_drop,
                    payload_type=PAYLOAD_TYPE,
                ):
                    with dpg.node_editor(
                        tag=NODE_EDITOR,
                        minimap=True,
                        minimap_location=dpg.mvNodeMiniMap_Location_BottomRight,
                    ):
                        dpg.add_node(tag=REF_NODE, label="", show=False)
                        dpg.bind_item_theme(REF_NODE, ref_theme)

    viewport_kwargs: dict[str, object] = {}
    if viewport_pos is not None:
        viewport_kwargs = {"x_pos": int(viewport_pos[0]), "y_pos": int(viewport_pos[1])}
    dpg.create_viewport(
        title="MegaDesk Canvas", width=width, height=height, **viewport_kwargs
    )
    dpg.setup_dearpygui()
    dpg.show_viewport()
    dpg.set_primary_window(GRAPH_WINDOW, True)

    engine.build_sidebar()
    if graph_bar:
        build_graph_bar(engine, GRAPH_BAR_TAG)
    if supervisor_panel:
        build_supervisor_panel()
    engine.on_viewport_resize()
    engine.host_all_members()

    def _on_resize(*_args: object) -> None:
        engine.on_viewport_resize()
        if supervisor_panel:
            reposition_supervisor_panel()

    dpg.set_viewport_resize_callback(_on_resize)
    return engine


def _attach_canvas_log_file(log: logging.Logger) -> None:
    """Append canvas diagnostics to the current Supervisor session's canvas.md."""
    try:
        attach_log_session()
        path = session_log_path("canvas")
        handler = logging.FileHandler(path, encoding="utf-8", errors="replace")
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        logging.getLogger().addHandler(handler)
        log.info("Canvas log %s", path)
    except Exception:
        log.exception("Could not attach canvas.md")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    log = logging.getLogger("megadesk.canvas")
    os.environ[ENV_CANVAS_ROOT] = str(Path(__file__).resolve().parent)
    discover_megadesk_frontends()

    # Supervisor BE is canvas-owned — start before the UI so LAUNCHREQUEST works.
    # Log session is Supervisor-generation scoped; reopen does not rotate files.
    if not ensure_supervisor_running():
        log.error("Supervisor BE failed to start (see Logs/CURRENT → supervisor.md)")
    _attach_canvas_log_file(log)

    model = GraphModel()
    try:
        model.load()
    except GraphError as exc:
        # Start empty rather than not at all: the graph bar can pick another file.
        log.error("Graph %s not loaded: %s", model.path, exc)

    engine = build_canvas(model)

    while dpg.is_dearpygui_running():
        engine.sync_members()
        dpg.render_dearpygui_frame()

    model.save()
    frame_pump.reset()
    dpg.destroy_context()


if __name__ == "__main__":
    main()
