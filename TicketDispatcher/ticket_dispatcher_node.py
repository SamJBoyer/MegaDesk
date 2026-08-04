"""MegaDesk.nodes entry point for TicketDispatcher (FE-only)."""

from __future__ import annotations

from pathlib import Path

from megadesk import BeSpec, FeSpec, Mode

from app import build_ui

_ICON = str((Path(__file__).resolve().parent / "icon.png"))


def get_exec_spec(mode: Mode) -> FeSpec | BeSpec | None:
    if mode == "FE":
        icon = _ICON if Path(_ICON).is_file() else None
        return FeSpec(
            name="ticket_dispatcher",
            description="Dispatch agent-ready GitHub issues onto the WORKORDER stream.",
            icon=icon,
            default_width=220,
            default_height=140,
            build=build_ui,
        )
    return None
