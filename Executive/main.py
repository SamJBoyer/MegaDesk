"""Whiteboard prototype — infinite Dear PyGui canvas with deployable nodes."""

from __future__ import annotations

import dearpygui.dearpygui as dpg

from engine.canvas_model import CanvasModel
from engine.display_engine import (
    CANVAS_WINDOW,
    DRAWLIST_TAG,
    DisplayEngine,
)
from engine.registry import discover_nodes


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
    discover_nodes()

    model = CanvasModel()
    model.load()

    engine = DisplayEngine(model)
    if model.layers:
        engine._target_layer_id = model.layers[0]["id"]

    dpg.create_context()
    _apply_daytime_theme()

    with dpg.handler_registry():
        dpg.add_mouse_wheel_handler(callback=engine.on_mouse_wheel)
        dpg.add_mouse_click_handler(callback=engine.on_mouse_click)
        dpg.add_mouse_double_click_handler(callback=engine.on_mouse_double_click)
        dpg.add_mouse_drag_handler(
            button=dpg.mvMouseButton_Left,
            threshold=1,
            callback=engine.on_mouse_drag,
        )
        dpg.add_mouse_drag_handler(
            button=dpg.mvMouseButton_Right,
            threshold=1,
            callback=engine.on_mouse_drag,
        )
        dpg.add_mouse_release_handler(callback=engine.on_mouse_release)
        dpg.add_key_press_handler(callback=engine.on_key_press)

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
        dpg.add_drawlist(tag=DRAWLIST_TAG, width=1280, height=800)

    dpg.create_viewport(title="Canvas2 Whiteboard", width=1280, height=800)
    dpg.setup_dearpygui()
    dpg.show_viewport()
    dpg.set_primary_window(CANVAS_WINDOW, True)

    engine.build_sidebar()
    engine.refresh_layer_bar()
    engine.refresh_hierarchy_panel()
    engine.refresh_terms_panel()
    engine.on_viewport_resize()
    engine.redraw()

    dpg.set_viewport_resize_callback(lambda *args: engine.on_viewport_resize())

    while dpg.is_dearpygui_running():
        # Keep floating chrome above the primary canvas without stealing focus every frame
        dpg.render_dearpygui_frame()

    model.save()
    dpg.destroy_context()


if __name__ == "__main__":
    main()
