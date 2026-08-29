"""MegaDesk.nodes entry point for WorkDispatcher (FE-only)."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Optional

from megadesk_contracts import BeSpec, FeSpec, load_parameter_names, normalize_parameters

from work_dispatcher_app import build_ui, read_parameters

_ICON = str(
    Path(__file__).resolve().parent / "Etc" / "Artwork" / "ticket.png"
)

# Names this node recognizes, declared in parameters.yaml: GIT_URL, ISSUE_LABEL.
PARAMETERS = load_parameter_names(__file__)


def get_fe_spec(
    parameters: Optional[Mapping[str, str]] = None,
) -> FeSpec | None:
    """FE spec, seeded with the repo and target label a graph saved for this member."""
    icon = _ICON if Path(_ICON).is_file() else None
    values = normalize_parameters(parameters, PARAMETERS)

    def build(parent: str, **kwargs: object) -> None:
        build_ui(parent, parameters=values, **kwargs)  # type: ignore[arg-type]

    return FeSpec(
        name="work_dispatcher",
        description="Dispatch labeled GitHub issues to a machine or cloud factory.",
        icon=icon,
        default_width=480,
        default_height=160,
        build=build,
        parameters=PARAMETERS,
        read_parameters=read_parameters,
    )


def get_be_spec() -> BeSpec | None:
    return None
