"""Discover and register node types from built-ins and MegaDesk FE plugins.

* ``nodes/`` — default BaseNode types shipped with MegaDesk (sticky, container, …)
* entry points ``MegaDesk.nodes`` — thin FeSpec tools (see ``megadesk_registry``)
* legacy ``executive.nodes`` — still accepted for BaseNode plugins
"""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
from collections.abc import Iterable, Sequence
from typing import Any, Type

from engine.base_node import BaseNode

logger = logging.getLogger(__name__)

# importlib.metadata entry-point group scanned at startup
ENTRY_POINT_GROUP = "executive.nodes"

_REGISTRY: dict[str, Type[BaseNode]] = {}


def register(node_cls: Type[BaseNode]) -> Type[BaseNode]:
    """Register a ``BaseNode`` subclass for sidebar placement and canvas load."""
    if not isinstance(node_cls, type) or not issubclass(node_cls, BaseNode):
        raise TypeError(f"{node_cls!r} must be a BaseNode subclass")
    if node_cls is BaseNode:
        raise TypeError("Cannot register BaseNode itself; subclass it")

    guid = getattr(node_cls, "global_guid", None)
    if not guid or not isinstance(guid, str):
        raise ValueError(f"{node_cls.__name__} missing non-empty string global_guid")

    nickname = getattr(node_cls, "nickname", None)
    if not nickname or not isinstance(nickname, str):
        raise ValueError(f"{node_cls.__name__} missing non-empty string nickname")

    # Ensure the concrete type implements draw (not left abstract).
    if inspect.isabstract(node_cls):
        abstract = sorted(getattr(node_cls, "__abstractmethods__", set()))
        raise TypeError(
            f"{node_cls.__name__} is abstract; implement: {', '.join(abstract)}"
        )

    icon = getattr(node_cls, "icon", "") or ""
    if isinstance(icon, str) and icon.strip() and node_cls.resolve_icon_path() is None:
        logger.warning(
            "%s icon path %r is missing or invalid; Drop-in will use the default "
            "black square",
            node_cls.__name__,
            icon,
        )

    existing = _REGISTRY.get(guid)
    if existing is not None and existing is not node_cls:
        logger.warning(
            "Replacing node type %r (%s -> %s)",
            guid,
            existing.__name__,
            node_cls.__name__,
        )
    _REGISTRY[guid] = node_cls
    return node_cls


def get_node_class(type_guid: str) -> Type[BaseNode] | None:
    return _REGISTRY.get(type_guid)


def all_node_types() -> list[Type[BaseNode]]:
    return list(_REGISTRY.values())


def _discover_builtin_nodes() -> None:
    """Import every subpackage under nodes/ so types self-register."""
    import nodes  # noqa: F401

    package = nodes
    for mod_info in pkgutil.iter_modules(package.__path__, package.__name__ + "."):
        importlib.import_module(mod_info.name)
        try:
            importlib.import_module(mod_info.name + ".node")
        except ModuleNotFoundError:
            pass


def _iter_entry_points(group: str):
    from importlib.metadata import entry_points

    eps = entry_points()
    if hasattr(eps, "select"):
        return list(eps.select(group=group))
    return list(eps.get(group, []))  # type: ignore[arg-type]


def _register_loaded(obj: Any, *, source: str) -> None:
    """Register a BaseNode subclass, or the result of a loader callable."""
    if isinstance(obj, type) and issubclass(obj, BaseNode):
        register(obj)
        logger.info("Registered pip node %s from %s", obj.global_guid, source)
        return

    if callable(obj) and not isinstance(obj, type):
        result = obj()
        _register_loaded_result(result, source=source)
        return

    raise TypeError(
        f"entry point {source} must resolve to a BaseNode subclass "
        f"or a callable returning class(es); got {obj!r}"
    )


def _register_loaded_result(result: Any, *, source: str) -> None:
    if isinstance(result, type) and issubclass(result, BaseNode):
        register(result)
        logger.info("Registered pip node %s from %s", result.global_guid, source)
        return

    if isinstance(result, Sequence) and not isinstance(result, (str, bytes)):
        for item in result:
            if not isinstance(item, type) or not issubclass(item, BaseNode):
                raise TypeError(
                    f"entry point {source} returned non-BaseNode item: {item!r}"
                )
            register(item)
            logger.info("Registered pip node %s from %s", item.global_guid, source)
        return

    if isinstance(result, Iterable) and not isinstance(result, (str, bytes)):
        for item in result:
            if not isinstance(item, type) or not issubclass(item, BaseNode):
                raise TypeError(
                    f"entry point {source} returned non-BaseNode item: {item!r}"
                )
            register(item)
            logger.info("Registered pip node %s from %s", item.global_guid, source)
        return

    raise TypeError(
        f"entry point {source} callable must return a BaseNode subclass "
        f"or sequence of subclasses; got {result!r}"
    )


def _discover_pip_nodes() -> None:
    """Load BaseNode types advertised by installed packages (entry points)."""
    for ep in _iter_entry_points(ENTRY_POINT_GROUP):
        source = f"{ep.name} ({ep.value})"
        try:
            plugin = ep.load()
            _register_loaded(plugin, source=source)
        except Exception:
            logger.exception(
                "Failed to load canvas node entry point %r (%s)",
                ep.name,
                getattr(ep, "value", ep),
            )


def discover_nodes() -> None:
    """Register built-ins, legacy BaseNode plugins, and MegaDesk FE specs."""
    from engine.megadesk_registry import discover_megadesk_frontends

    _discover_builtin_nodes()
    _discover_pip_nodes()
    discover_megadesk_frontends()


def create_node(type_guid: str, **kwargs) -> BaseNode:
    cls = get_node_class(type_guid)
    if cls is None:
        raise KeyError(f"Unknown node type: {type_guid}")
    return cls(**kwargs)
