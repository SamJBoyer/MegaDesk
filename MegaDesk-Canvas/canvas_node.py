"""MegaDesk.nodes entry point for canvas voice tools (no FE / BE)."""

from __future__ import annotations

from megadesk_contracts import BeSpec, FeSpec, ToolSpec


def get_fe_spec() -> FeSpec | None:
    return None


def get_be_spec() -> BeSpec | None:
    return None


def get_tool_spec() -> ToolSpec | None:
    from canvas_tools import tool_spec

    return tool_spec()
