"""MegaDesk.nodes entry point for GraphScope (FE-only)."""

from __future__ import annotations

from pathlib import Path

from megadesk_contracts import BeSpec, FeSpec

from graph_scope_frontend.app import build_ui

_ICON = str(Path(__file__).resolve().parent / "Etc" / "Artwork" / "icon.png")
NODE_NAME = "graph_scope"


def get_fe_spec() -> FeSpec | None:
    icon = _ICON if Path(_ICON).is_file() else None
    return FeSpec(
        name=NODE_NAME,
        description="Watch AgentHandler work-graph runs.",
        icon=icon,
        default_width=480,
        default_height=240,
        build=build_ui,
    )


def get_be_spec() -> BeSpec | None:
    return None

