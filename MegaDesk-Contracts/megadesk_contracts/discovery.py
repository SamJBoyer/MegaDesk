"""Discover MegaDesk.nodes entry points and resolve FE/BE specs.

Each entry point names the node module. Discovery loads that module and calls
``get_fe_spec`` / ``get_be_spec``. One entry point serves both halves: return
``None`` from the function for a mode the node does not implement.
"""

from __future__ import annotations

import inspect
import logging
from importlib.metadata import entry_points
from types import ModuleType
from typing import Any, Callable, Mapping, Optional

from megadesk_contracts.exec_spec import BeSpec, FeSpec

log = logging.getLogger("megadesk_contracts.discovery")

ENTRY_POINT_GROUP = "MegaDesk.nodes"


def _iter_entry_points():
    return list(entry_points().select(group=ENTRY_POINT_GROUP))


def _module_of(loaded: object) -> Optional[ModuleType]:
    if isinstance(loaded, ModuleType):
        return loaded
    if callable(loaded):
        return inspect.getmodule(loaded)
    return None


def _takes_parameters(fn: Callable[..., Any]) -> bool:
    """Whether ``fn`` accepts a ``parameters`` keyword.

    Nodes without parameters keep a zero-argument ``get_fe_spec``, so the caller
    must not force the keyword on them.
    """
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return False
    for param in signature.parameters.values():
        if param.kind is inspect.Parameter.VAR_KEYWORD:
            return True
        if param.name == "parameters" and param.kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            return True
    return False


def _call_spec_fn(
    fn: Callable[..., Any],
    parameters: Optional[Mapping[str, str]] = None,
) -> Any:
    if parameters is not None and _takes_parameters(fn):
        return fn(parameters=parameters)
    return fn()


def _load_module(ep) -> Optional[ModuleType]:
    loaded = ep.load()
    mod = _module_of(loaded)
    if mod is None:
        log.error("MegaDesk.nodes entry %s did not resolve to a module", ep.name)
    return mod


def _call_fe(
    mod: Optional[ModuleType],
    parameters: Optional[Mapping[str, str]] = None,
) -> FeSpec | None:
    if mod is None or not callable(getattr(mod, "get_fe_spec", None)):
        return None
    spec = _call_spec_fn(mod.get_fe_spec, parameters=parameters)
    return spec if isinstance(spec, FeSpec) else None


def _call_be(mod: Optional[ModuleType]) -> BeSpec | None:
    if mod is None or not callable(getattr(mod, "get_be_spec", None)):
        return None
    spec = mod.get_be_spec()
    return spec if isinstance(spec, BeSpec) else None


def _scan(name: str, call) -> FeSpec | BeSpec | None:
    """Load every entry and return the first spec whose name matches ``name``."""
    entry_list = list(_iter_entry_points())
    ordered = [ep for ep in entry_list if ep.name == name]
    ordered += [ep for ep in entry_list if ep.name != name]

    for ep in ordered:
        try:
            spec = call(_load_module(ep))
        except Exception:
            log.exception("spec load failed for %s", ep.name)
            continue
        if spec is not None and name in (ep.name, spec.name):
            return spec
    return None


def load_fe_spec(
    name: str,
    parameters: Optional[Mapping[str, str]] = None,
) -> FeSpec | None:
    """Resolve one FE spec by name, built with ``parameters`` folded in.

    Matches the entry-point name first, then ``FeSpec.name`` — the two differ
    often enough (a graph stores ``FeSpec.name``) that scanning is worth it.
    """

    def call(mod: Optional[ModuleType]) -> FeSpec | None:
        return _call_fe(mod, parameters)

    spec = _scan(name, call)
    return spec if isinstance(spec, FeSpec) else None


def load_be_spec(name: str) -> BeSpec | None:
    """Resolve one BE spec by entry-point name or ``BeSpec.name``."""
    spec = _scan(name, _call_be)
    return spec if isinstance(spec, BeSpec) else None


def discover_frontends() -> dict[str, FeSpec]:
    """Return name → FeSpec for every installed node that exposes an FE."""
    out: dict[str, FeSpec] = {}
    for ep in _iter_entry_points():
        try:
            spec = _call_fe(_load_module(ep))
        except Exception:
            log.exception("FE discovery failed for %s", ep.name)
            continue
        if isinstance(spec, FeSpec):
            out[spec.name or ep.name] = spec
    return out


def discover_backends() -> dict[str, BeSpec]:
    """Return name → BeSpec for every installed node that exposes a BE."""
    out: dict[str, BeSpec] = {}
    for ep in _iter_entry_points():
        try:
            spec = _call_be(_load_module(ep))
        except Exception:
            log.exception("BE discovery failed for %s", ep.name)
            continue
        if isinstance(spec, BeSpec):
            out[spec.name or ep.name] = spec
    return out


def get_backend(name: str) -> Optional[BeSpec]:
    """Resolve a BE spec by node name (entry-point name or BeSpec.name)."""
    return load_be_spec(name)
