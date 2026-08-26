"""Shared Dear PyGui frame pump so multiple embedded FE tools can drain UI."""

from __future__ import annotations

from typing import Callable

_callbacks: list[Callable[[], None]] = []
_armed = False


def register(callback: Callable[[], None]) -> None:
    """Register a per-frame callback (idempotent arm of the shared pump)."""
    import dearpygui.dearpygui as dpg

    global _armed
    if callback not in _callbacks:
        _callbacks.append(callback)
    if _armed:
        return
    _armed = True

    def _pump() -> None:
        for cb in list(_callbacks):
            try:
                cb()
            except Exception:
                pass
        if dpg.is_dearpygui_running():
            dpg.set_frame_callback(dpg.get_frame_count() + 1, _pump)

    # Relative, not the literal frame 1: an absolute arm is silently dropped when
    # the first registration happens after frame 1 (first node on an empty board).
    dpg.set_frame_callback(dpg.get_frame_count() + 1, _pump)


def unregister(callback: Callable[[], None]) -> None:
    try:
        _callbacks.remove(callback)
    except ValueError:
        pass


def reset() -> None:
    """Drop all callbacks and disarm — call on DPG context teardown.

    Module state outlives ``destroy_context()``, so without this a second
    context in the same process stays armed with callbacks bound to widgets
    that no longer exist and never gets a pump tick.
    """
    global _armed
    _callbacks.clear()
    _armed = False
