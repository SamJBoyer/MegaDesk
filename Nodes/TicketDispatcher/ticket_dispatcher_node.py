"""MegaDesk.nodes entry point for TicketDispatcher (FE-only)."""

from __future__ import annotations

from pathlib import Path

from megadesk_contracts import BeSpec, FeSpec, Mode

from ticket_dispatcher_app import build_ui

_ICON = str(
    Path(__file__).resolve().parent / "Etc" / "Artwork" / "ticket.png"
)


def get_fe_spec() -> FeSpec | None:
    icon = _ICON if Path(_ICON).is_file() else None
    return FeSpec(
        name="ticket_dispatcher",
        description="Dispatch agent-ready GitHub issues onto the WORKORDER stream.",
        icon=icon,
        default_width=480,
        default_height=160,
        build=build_ui,
    )


def get_be_spec() -> BeSpec | None:
    return None


def get_exec_spec(mode: Mode) -> FeSpec | BeSpec | None:
    if mode == "FE":
        return get_fe_spec()
    return None
