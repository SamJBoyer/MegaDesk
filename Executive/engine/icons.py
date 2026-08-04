"""Node icon textures for the Drop-in panel.

Every registered node type gets a Dear PyGui texture. If ``BaseNode.icon`` is
empty or does not resolve to a loadable image file, a solid black square is used.
"""

from __future__ import annotations

import logging
from typing import Type

import dearpygui.dearpygui as dpg

from engine.base_node import BaseNode

logger = logging.getLogger(__name__)

ICON_PX = 48
_REGISTRY_TAG = "executive_icon_texture_registry"
_DEFAULT_TAG = "executive_default_node_icon"
_CACHE: dict[str, int | str] = {}


def _ensure_registry() -> None:
    if not dpg.does_item_exist(_REGISTRY_TAG):
        dpg.add_texture_registry(tag=_REGISTRY_TAG)


def _black_square_data(size: int = ICON_PX) -> list[float]:
    # RGBA floats in [0, 1] — solid black, fully opaque.
    pixel = (0.0, 0.0, 0.0, 1.0)
    out: list[float] = []
    for _ in range(size * size):
        out.extend(pixel)
    return out


def _ensure_default_texture() -> int | str:
    _ensure_registry()
    if dpg.does_item_exist(_DEFAULT_TAG):
        return _DEFAULT_TAG
    dpg.add_static_texture(
        ICON_PX,
        ICON_PX,
        _black_square_data(ICON_PX),
        tag=_DEFAULT_TAG,
        parent=_REGISTRY_TAG,
    )
    _CACHE["__default__"] = _DEFAULT_TAG
    return _DEFAULT_TAG


def get_icon_texture(node_cls: Type[BaseNode]) -> int | str:
    """Return a texture tag for ``node_cls`` (cached; falls back to black square)."""
    path = node_cls.resolve_icon_path()
    cache_key = path or "__default__"
    existing = _CACHE.get(cache_key)
    if existing is not None and dpg.does_item_exist(existing):
        return existing

    if path is None:
        return _ensure_default_texture()

    _ensure_registry()
    try:
        width, height, _channels, data = dpg.load_image(path)
    except Exception:
        logger.warning(
            "Failed to load icon for %s (%r); using default black square",
            getattr(node_cls, "global_guid", node_cls.__name__),
            path,
        )
        return _ensure_default_texture()

    tag = f"executive_node_icon::{node_cls.global_guid}"
    if dpg.does_item_exist(tag):
        dpg.delete_item(tag)

    dpg.add_static_texture(
        width,
        height,
        data,
        tag=tag,
        parent=_REGISTRY_TAG,
    )
    _CACHE[cache_key] = tag
    return tag
