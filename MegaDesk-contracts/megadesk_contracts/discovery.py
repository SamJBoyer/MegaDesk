"""Discover MegaDesk.nodes entry points and resolve FE/BE specs."""

from __future__ import annotations

import inspect
import logging
from types import ModuleType
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


def _module_of(loaded: object) -> Optional[ModuleType]:
    if isinstance(loaded, ModuleType):
        return loaded
    if callable(loaded):
        return inspect.getmodule(loaded)
    return None


def _call_fe(mod: Optional[ModuleType], loaded: object, ep_name: str) -> FeSpec | None:
    if mod is not None and callable(getattr(mod, "get_fe_spec", None)):
        spec = mod.get_fe_spec()
        return spec if isinstance(spec, FeSpec) else None
    if callable(loaded):
        spec = loaded("FE")
        return spec if isinstance(spec, FeSpec) else None
    log.error("MegaDesk.nodes entry %s has no get_fe_spec", ep_name)
    return None


def _call_be(mod: Optional[ModuleType], loaded: object, ep_name: str) -> BeSpec | None:
    if mod is not None and callable(getattr(mod, "get_be_spec", None)):
        spec = mod.get_be_spec()
        return spec if isinstance(spec, BeSpec) else None
    if callable(loaded):
        spec = loaded("BE")
        return spec if isinstance(spec, BeSpec) else None
    return None


def load_exec_spec(name: str, mode: Mode) -> FeSpec | BeSpec | None:
    """Load one entry point by name and return its FE or BE spec."""
    for ep in _iter_entry_points():
        if ep.name != name:
            continue
        try:
            loaded = ep.load()
        except Exception:
            log.exception("Failed to load MegaDesk.nodes entry point %s", name)
            return None
        mod = _module_of(loaded)
        try:
            if mode == "FE":
                return _call_fe(mod, loaded, name)
            if mode == "BE":
                return _call_be(mod, loaded, name)
        except Exception:
            log.exception("spec load (%r) failed for %s", mode, name)
            return None
        return None
    return None


def discover_frontends() -> dict[str, FeSpec]:
    """Return name → FeSpec for every installed node that exposes an FE."""
    out: dict[str, FeSpec] = {}
    for ep in _iter_entry_points():
        try:
            loaded = ep.load()
            spec = _call_fe(_module_of(loaded), loaded, ep.name)
        except Exception:
            log.exception("FE discovery failed for %s", ep.name)
            continue
        if isinstance(spec, FeSpec):
            key = spec.name or ep.name
            out[key] = spec
    return out


def discover_backends() -> dict[str, BeSpec]:
    """Return name → BeSpec for every installed node that exposes a BE."""
    out: dict[str, BeSpec] = {}
    for ep in _iter_entry_points():
        try:
            loaded = ep.load()
            spec = _call_be(_module_of(loaded), loaded, ep.name)
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
    spec = load_exec_spec(name, "BE")
    return spec if isinstance(spec, BeSpec) else None


def has_backend(name: str) -> bool:
    return get_backend(name) is not None


def backends_for_frontend(spec: FeSpec) -> tuple[str, ...]:
    """LAUNCHREQUEST ``node_endpoint`` values to fire when this FE is hosted."""
    if spec.backends:
        return tuple(spec.backends)
    return ()
