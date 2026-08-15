"""MegaDesk.nodes FE discovery for the canvas host."""

from __future__ import annotations

import logging

from megadesk_contracts import FeSpec, discover_frontends

logger = logging.getLogger(__name__)

_FRONTENDS: dict[str, FeSpec] = {}


def discover_megadesk_frontends() -> None:
    """Refresh the in-memory FE catalog from installed MegaDesk.nodes."""
    global _FRONTENDS
    _FRONTENDS = discover_frontends()
    logger.info(
        "Discovered %d MegaDesk FE node(s): %s",
        len(_FRONTENDS),
        ", ".join(sorted(_FRONTENDS)) or "(none)",
    )


def all_fe_specs() -> list[FeSpec]:
    return list(_FRONTENDS.values())


def get_fe_spec(name: str) -> FeSpec | None:
    return _FRONTENDS.get(name)


def fe_has_backend(name: str) -> bool:
    spec = _FRONTENDS.get(name)
    return bool(spec and spec.backends)


PALETTE_PREFIX = "megadesk:"


def palette_key(name: str) -> str:
    return f"{PALETTE_PREFIX}{name}"


def parse_palette_key(key: str) -> str | None:
    if key.startswith(PALETTE_PREFIX):
        return key[len(PALETTE_PREFIX) :]
    return None
