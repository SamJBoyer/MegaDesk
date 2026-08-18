"""MegaDesk.nodes entry point for TicketDispatcher (FE-only)."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Optional

from megadesk_contracts import BeSpec, FeSpec, Mode, load_parameter_names, normalize_parameters

from ticket_dispatcher_app import build_ui, read_parameters

_ICON = str(
    Path(__file__).resolve().parent / "Etc" / "Artwork" / "ticket.png"
)

# Names this node recognizes, declared in parameters.yaml: GIT_URL.
PARAMETERS = load_parameter_names(__file__)


def get_fe_spec(
    parameters: Optional[Mapping[str, str]] = None,
) -> FeSpec | None:
    """FE spec, seeded with the repo URL a graph saved for this member."""
    icon = _ICON if Path(_ICON).is_file() else None
    values = normalize_parameters(parameters, PARAMETERS)

    def build(parent: str, **kwargs: object) -> None:
        build_ui(parent, parameters=values, **kwargs)  # type: ignore[arg-type]

    return FeSpec(
        name="ticket_dispatcher",
        description="Dispatch agent-ready GitHub issues to both factories.",
        icon=icon,
        default_width=480,
        default_height=160,
        build=build,
        parameters=PARAMETERS,
        read_parameters=read_parameters,
    )


def get_be_spec() -> BeSpec | None:
    return None


def get_exec_spec(
    mode: Mode,
    parameters: Optional[Mapping[str, str]] = None,
) -> FeSpec | BeSpec | None:
    if mode == "FE":
        return get_fe_spec(parameters)
    return None
