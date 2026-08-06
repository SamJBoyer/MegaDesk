"""Icon textures for the Catalog panel.

Every catalog FE entry gets a Dear PyGui texture. If the icon path is empty
or does not resolve to a loadable image file, a solid black square is used.
"""

from __future__ import annotations

import logging

import dearpygui.dearpygui as dpg

logger = logging.getLogger(__name__)

ICON_PX = 48
_REGISTRY_TAG = "catalog_icon_texture_registry"
_DEFAULT_TAG = "catalog_default_node_icon"
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


def get_icon_texture_for_path(path: str | None, *, tag_suffix: str) -> int | str:
    """Return a texture for an image path (cached; falls back to black square)."""
    cache_key = path or "__default__"
    existing = _CACHE.get(cache_key)
    if existing is not None and dpg.does_item_exist(existing):
        return existing

    if not path:
        return _ensure_default_texture()

    from pathlib import Path

    if not Path(path).is_file():
        return _ensure_default_texture()

    _ensure_registry()
    try:
        width, height, _channels, data = dpg.load_image(path)
    except Exception:
        logger.warning(
            "Failed to load icon %r (%s); using default black square",
            path,
            tag_suffix,
        )
        return _ensure_default_texture()

    tag = f"catalog_node_icon::{tag_suffix}"
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
