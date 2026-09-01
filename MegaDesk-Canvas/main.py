"""MegaDesk canvas — Dear PyGui node_editor hosting a graph of MegaDesk FE nodes."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import dearpygui.dearpygui as dpg
from megadesk_contracts import (
    ENV_CANVAS_ROOT,
    dev_flush_mode_enabled,
    ensure_supervisor_running,
    flush_live_redis_pair,
    frame_pump,
)
from megadesk_contracts.log_session import attach_log_session, session_log_path

from engine.display_engine import (
    CATALOG_BODY_TAG,
    CATALOG_TOGGLE_TAG,
    CATALOG_WIDTH,
    CANVAS_BODY_TAG,
    EDITOR_HOST_TAG,
    GRAPH_WINDOW,
    NODE_EDITOR,
    PAYLOAD_TYPE,
    REF_NODE,
    SIDEBAR_TAG,
    SUPERVISOR_BODY_TAG,
    SUPERVISOR_PANEL_TAG,
    SUPERVISOR_TOGGLE_TAG,
    SUPERVISOR_WIDTH,
    VOICE_DECK_BODY_TAG,
    VOICE_DECK_HEIGHT,
    VOICE_DECK_PANEL_TAG,
    VOICE_DECK_TOGGLE_TAG,
    DisplayEngine,
)
from engine.graph_bar import BAR_HEIGHT, GRAPH_BAR_TAG, build_graph_bar
from engine.graph_model import GraphError, GraphModel, remember_last_graph
from engine.megadesk_registry import discover_megadesk_frontends
from supervisor.panel import build_supervisor_panel, show_logs_for_canvas_node
from voice_deck.panel import (
    build_voice_deck_panel,
    ensure_voice_deck_running,
    shutdown_voice_deck_panel,
)


def _apply_daytime_theme() -> None:
    """Light sage-green daytime chrome for panels, widgets, and the node grid."""
    with dpg.theme() as theme:
        with dpg.theme_component(dpg.mvAll):
            dpg.add_theme_color(dpg.mvThemeCol_WindowBg, (236, 246, 238, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (242, 250, 244, 255))
            dpg.add_theme_color(dpg.mvThemeCol_PopupBg, (252, 255, 252, 250))
            dpg.add_theme_color(dpg.mvThemeCol_Border, (176, 204, 184, 255))
            dpg.add_theme_color(dpg.mvThemeCol_Text, (28, 40, 32, 255))
            dpg.add_theme_color(dpg.mvThemeCol_TextDisabled, (108, 128, 114, 255))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBg, (255, 255, 255, 255))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, (220, 240, 226, 255))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgActive, (198, 228, 206, 255))
            dpg.add_theme_color(dpg.mvThemeCol_TitleBg, (214, 234, 218, 255))
            dpg.add_theme_color(dpg.mvThemeCol_TitleBgActive, (196, 224, 204, 255))
            dpg.add_theme_color(dpg.mvThemeCol_Button, (224, 240, 226, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (200, 228, 208, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (168, 208, 178, 255))
            dpg.add_theme_color(dpg.mvThemeCol_Header, (210, 234, 216, 255))
            dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered, (190, 222, 198, 255))
            dpg.add_theme_color(dpg.mvThemeCol_HeaderActive, (168, 208, 178, 255))
            dpg.add_theme_color(dpg.mvThemeCol_CheckMark, (40, 140, 72, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarBg, (232, 244, 234, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrab, (168, 196, 174, 255))
            dpg.add_theme_color(dpg.mvThemeCol_Separator, (176, 204, 184, 255))
            dpg.add_theme_color(dpg.mvThemeCol_Tab, (214, 234, 218, 255))
            dpg.add_theme_color(dpg.mvThemeCol_TabHovered, (190, 222, 198, 255))
            dpg.add_theme_color(dpg.mvThemeCol_TabActive, (236, 246, 238, 255))
            dpg.add_theme_color(dpg.mvThemeCol_SliderGrab, (72, 160, 96, 255))
            dpg.add_theme_color(dpg.mvThemeCol_SliderGrabActive, (48, 140, 80, 255))
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 4)
            dpg.add_theme_style(dpg.mvStyleVar_WindowRounding, 6)
            dpg.add_theme_color(
                dpg.mvNodeCol_GridBackground,
                (228, 242, 230, 255),
                category=dpg.mvThemeCat_Nodes,
            )
            dpg.add_theme_color(
                dpg.mvNodeCol_GridLine,
                (196, 218, 200, 255),
                category=dpg.mvThemeCat_Nodes,
            )
            dpg.add_theme_color(
                dpg.mvNodeCol_NodeBackground,
                (250, 254, 250, 255),
                category=dpg.mvThemeCat_Nodes,
            )
            dpg.add_theme_color(
                dpg.mvNodeCol_NodeBackgroundHovered,
                (240, 250, 242, 255),
                category=dpg.mvThemeCat_Nodes,
            )
            dpg.add_theme_color(
                dpg.mvNodeCol_NodeBackgroundSelected,
                (220, 240, 226, 255),
                category=dpg.mvThemeCat_Nodes,
            )
            dpg.add_theme_color(
                dpg.mvNodeCol_NodeOutline,
                (160, 188, 168, 255),
                category=dpg.mvThemeCat_Nodes,
            )
            dpg.add_theme_color(
                dpg.mvNodeCol_TitleBar,
                (186, 218, 192, 255),
                category=dpg.mvThemeCat_Nodes,
            )
            dpg.add_theme_color(
                dpg.mvNodeCol_TitleBarHovered,
                (166, 206, 174, 255),
                category=dpg.mvThemeCat_Nodes,
            )
            dpg.add_theme_color(
                dpg.mvNodeCol_TitleBarSelected,
                (146, 190, 156, 255),
                category=dpg.mvThemeCat_Nodes,
            )
            dpg.add_theme_color(
                dpg.mvNodeCol_Link, (48, 140, 80, 255), category=dpg.mvThemeCat_Nodes
            )
            dpg.add_theme_color(
                dpg.mvNodeCol_LinkHovered,
                (72, 160, 96, 255),
                category=dpg.mvThemeCat_Nodes,
            )
            dpg.add_theme_color(
                dpg.mvNodeCol_Pin, (48, 140, 80, 255), category=dpg.mvThemeCat_Nodes
            )
            dpg.add_theme_color(
                dpg.mvNodesCol_MiniMapBackground,
                (220, 236, 224, 220),
                category=dpg.mvThemeCat_Nodes,
            )
            dpg.add_theme_color(
                dpg.mvNodesCol_MiniMapCanvas,
                (236, 246, 238, 255),
                category=dpg.mvThemeCat_Nodes,
            )
            dpg.add_theme_color(
                dpg.mvNodesCol_MiniMapNodeBackground,
                (168, 208, 178, 255),
                category=dpg.mvThemeCat_Nodes,
            )
            dpg.add_theme_color(
                dpg.mvNodesCol_MiniMapOutline,
                (160, 188, 168, 255),
                category=dpg.mvThemeCat_Nodes,
            )
            dpg.add_theme_style(
                dpg.mvNodeStyleVar_NodeCornerRounding,
                6,
                category=dpg.mvThemeCat_Nodes,
            )
    dpg.bind_theme(theme)


def build_canvas(
    model: GraphModel,
    *,
    width: int = 1280,
    height: int = 800,
    viewport_pos: tuple[int, int] | None = None,
    supervisor_panel: bool = True,
    voice_deck_panel: bool = True,
    graph_bar: bool = True,
) -> DisplayEngine:
    """Construct the canvas UI for ``model`` and return its engine.

    Everything from ``create_context()`` up to (but excluding) the render loop.
    Callers own discovery, ``model.load()``, the loop, and ``destroy_context()``.
    ``viewport_pos`` places the viewport off-screen for harnessed runs — it must
    still be shown, since a minimized viewport renders nothing.
    """
    engine = DisplayEngine(model)
    engine.supervisor_enabled = supervisor_panel
    engine.voice_deck_enabled = voice_deck_panel

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
        with dpg.group(horizontal=True, tag=CANVAS_BODY_TAG):
            with dpg.child_window(
                tag=SIDEBAR_TAG,
                width=CATALOG_WIDTH,
                border=True,
                no_scrollbar=True,
            ):
                dpg.add_button(
                    tag=CATALOG_TOGGLE_TAG,
                    label="<",
                    width=18,
                    callback=lambda: engine.toggle_catalog(),
                )
                dpg.add_child_window(
                    tag=CATALOG_BODY_TAG,
                    width=-1,
                    height=-1,
                    border=False,
                    no_scrollbar=False,
                )
            with dpg.child_window(
                tag=EDITOR_HOST_TAG,
                width=-1,
                border=True,
                no_scrollbar=True,
            ):
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
            if supervisor_panel:
                with dpg.child_window(
                    tag=SUPERVISOR_PANEL_TAG,
                    width=SUPERVISOR_WIDTH,
                    border=True,
                    no_scrollbar=True,
                ):
                    dpg.add_button(
                        tag=SUPERVISOR_TOGGLE_TAG,
                        label=">",
                        width=18,
                        callback=lambda: engine.toggle_supervisor(),
                    )
                    dpg.add_child_window(
                        tag=SUPERVISOR_BODY_TAG,
                        width=-1,
                        height=-1,
                        border=False,
                        no_scrollbar=False,
                    )
        if voice_deck_panel:
            with dpg.child_window(
                tag=VOICE_DECK_PANEL_TAG,
                width=-1,
                height=VOICE_DECK_HEIGHT,
                border=True,
                no_scrollbar=True,
            ):
                with dpg.group(horizontal=True):
                    dpg.add_button(
                        tag=VOICE_DECK_TOGGLE_TAG,
                        label="v",
                        width=18,
                        callback=lambda: engine.toggle_voice_deck(),
                    )
                    dpg.add_child_window(
                        tag=VOICE_DECK_BODY_TAG,
                        width=-1,
                        height=-1,
                        border=False,
                        no_scrollbar=False,
                    )

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
        engine.on_member_selected = show_logs_for_canvas_node
        build_supervisor_panel(SUPERVISOR_BODY_TAG)
    if voice_deck_panel:
        build_voice_deck_panel(VOICE_DECK_BODY_TAG)
    engine.on_viewport_resize()
    engine.host_all_members()

    def _on_resize(*_args: object) -> None:
        engine.on_viewport_resize()

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
    # DEV_FLUSH_MODE (default on) empties live 0/1 first so the new supervisor
    # recreates consumer groups and SUPERVISOR:ALIVE / SINGLETON on a fresh pair.
    if dev_flush_mode_enabled():
        log.warning("DEV_FLUSH_MODE: flushing Redis DB 0 and DB 1")
        try:
            flush_live_redis_pair()
        except Exception:
            log.exception("DEV_FLUSH_MODE: Redis flush failed")
    if not ensure_supervisor_running():
        log.error("Supervisor BE failed to start (see Logs/CURRENT → supervisor.md)")
    elif not ensure_voice_deck_running():
        log.warning("VoiceDeck BE failed to start (see Logs/CURRENT → voice_deck.md)")
    _attach_canvas_log_file(log)

    model = GraphModel()
    try:
        model.load()
        remember_last_graph(model.path)
    except GraphError as exc:
        # Start empty rather than not at all: the graph bar can pick another file.
        log.error("Graph %s not loaded: %s", model.path, exc)

    from engine.canvas_api import attach_canvas_api

    engine = build_canvas(model)
    attach_canvas_api(engine)

    while dpg.is_dearpygui_running():
        engine.sync_members()
        api = getattr(engine, "canvas_api", None)
        if api is not None:
            api.drain_commands()
        dpg.render_dearpygui_frame()

    model.save()
    api = getattr(engine, "canvas_api", None)
    if api is not None:
        api.detach()
    shutdown_voice_deck_panel()
    frame_pump.reset()
    dpg.destroy_context()


if __name__ == "__main__":
    main()
