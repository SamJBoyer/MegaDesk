"""Discover MegaDesk.nodes entry points and resolve FE/BE specs."""

from __future__ import annotations

import logging
from typing import Optional

from megadesk_contracts.exec_spec import BeSpec, FeSpec, Mode

log = logging.getLogger("megadesk_contracts.discovery")

ENTRY_POINT_GROUP = "MegaDesk.nodes"


def _iter_entry_points():
    try:
        from importlib.metadata import entry_points
    except ImportError:  # pragma: no cover
        return []

    eps = entry_points()
    if hasattr(eps, "select"):
        return list(eps.select(group=ENTRY_POINT_GROUP))
    return list(eps.get(ENTRY_POINT_GROUP, []))  # type: ignore[arg-type]


def load_exec_spec(name: str, mode: Mode) -> FeSpec | BeSpec | None:
    """Load one entry point by name and call get_exec_spec(mode)."""
    for ep in _iter_entry_points():
        if ep.name != name:
            continue
        try:
            fn = ep.load()
        except Exception:
            log.exception("Failed to load MegaDesk.nodes entry point %s", name)
            return None
        if not callable(fn):
            log.error("MegaDesk.nodes entry %s is not callable", name)
            return None
        try:
            return fn(mode)
        except Exception:
            log.exception("get_exec_spec(%r) failed for %s", mode, name)
            return None
    return None


def discover_frontends() -> dict[str, FeSpec]:
    """Return name → FeSpec for every installed node that exposes an FE."""
    out: dict[str, FeSpec] = {}
    for ep in _iter_entry_points():
        try:
            fn = ep.load()
            if not callable(fn):
                continue
            spec = fn("FE")
        except Exception:
            log.exception("FE discovery failed for %s", ep.name)
            continue
        if isinstance(spec, FeSpec):
            # Prefer FeSpec.name; fall back to entry-point name.
            key = spec.name or ep.name
            out[key] = spec
    return out


def discover_backends() -> dict[str, BeSpec]:
    """Return name → BeSpec for every installed node that exposes a BE."""
    out: dict[str, BeSpec] = {}
    for ep in _iter_entry_points():
        try:
            fn = ep.load()
            if not callable(fn):
                continue
            spec = fn("BE")
        except Exception:
            log.exception("BE discovery failed for %s", ep.name)
            continue
        if isinstance(spec, BeSpec):
            key = spec.name or ep.name
            out[key] = spec
    return out


def get_backend(name: str) -> Optional[BeSpec]:
    """Resolve a BE spec by node name (entry-point name or BeSpec.name)."""
    backends = discover_backends()
    if name in backends:
        return backends[name]
    # Also try loading the entry point directly by ep.name
    spec = load_exec_spec(name, "BE")
    return spec if isinstance(spec, BeSpec) else None


def has_backend(name: str) -> bool:
    return get_backend(name) is not None
