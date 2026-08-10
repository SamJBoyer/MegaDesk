"""MegaDesk canvas — Dear PyGui node_editor hosting MegaDesk FE nodes."""

from __future__ import annotations

import logging

import dearpygui.dearpygui as dpg
from megadesk_contracts import ensure_supervisor_running

from engine.canvas_model import CanvasModel
from engine.display_engine import (
    CANVAS_WINDOW,
    NODE_EDITOR,
    PAYLOAD_TYPE,
    REF_NODE,
    SIDEBAR_TAG,
    DisplayEngine,
)
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


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    log = logging.getLogger("megadesk.canvas")
    discover_megadesk_frontends()

    # Supervisor BE is canvas-owned — start before the UI so LAUNCHREQUEST works.
    if not ensure_supervisor_running():
        log.error(
            "Supervisor BE failed to start "
            "(see MegaDesk-Canvas/logs/supervisor/supervisor.log)"
        )

    model = CanvasModel()
    model.load()

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
        label="Canvas",
        tag=CANVAS_WINDOW,
        no_title_bar=True,
        no_move=True,
        no_resize=True,
        no_bring_to_front_on_focus=True,
        no_scrollbar=True,
        no_scroll_with_mouse=True,
        pos=(0, 0),
        width=1280,
        height=800,
    ):
        with dpg.group(horizontal=True):
            dpg.add_child_window(
                tag=SIDEBAR_TAG,
                width=240,
                border=True,
                no_scrollbar=False,
            )
            with dpg.child_window(width=-1, border=True, no_scrollbar=True):
                with dpg.group(
                    drop_callback=engine.on_canvas_drop,
                    payload_type=PAYLOAD_TYPE,
                ):
                    with dpg.node_editor(
                        tag=NODE_EDITOR,
                        minimap=True,
                        minimap_location=dpg.mvNodeMiniMap_Location_BottomRight,
                    ):
                        dpg.add_node(tag=REF_NODE, label="", show=False)
                        dpg.bind_item_theme(REF_NODE, ref_theme)

    dpg.create_viewport(title="MegaDesk Canvas", width=1280, height=800)
    dpg.setup_dearpygui()
    dpg.show_viewport()
    dpg.set_primary_window(CANVAS_WINDOW, True)

    engine.build_sidebar()
    build_supervisor_panel()
    engine.on_viewport_resize()
    engine.open_all_megadesk_guis()

    def _on_resize(*_args: object) -> None:
        engine.on_viewport_resize()
        reposition_supervisor_panel()

    dpg.set_viewport_resize_callback(_on_resize)

    while dpg.is_dearpygui_running():
        engine.sync_megadesk_nodes()
        dpg.render_dearpygui_frame()

    model.save()
    dpg.destroy_context()


if __name__ == "__main__":
    main()
