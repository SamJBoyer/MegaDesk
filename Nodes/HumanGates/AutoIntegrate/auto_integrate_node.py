"""MegaDesk.nodes entry point for AutoIntegrate (FE-only)."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Optional

from megadesk_contracts import BeSpec, FeSpec, load_parameter_names, normalize_parameters

from auto_integrate_app import build_ui, read_parameters

# Names this node recognizes, declared in parameters.yaml: GIT_URL.
PARAMETERS = load_parameter_names(__file__)


def get_fe_spec(
    parameters: Optional[Mapping[str, str]] = None,
) -> FeSpec | None:
    """FE spec, seeded with the repo URL a graph saved for this member."""
    values = normalize_parameters(parameters, PARAMETERS)

    def build(parent: str, **kwargs: object) -> None:
        build_ui(parent, parameters=values, **kwargs)  # type: ignore[arg-type]

    return FeSpec(
        name="auto_integrate",
        description="Send a factory agent at a pull request that no longer merges.",
        icon=None,
        default_width=520,
        default_height=160,
        build=build,
        parameters=PARAMETERS,
        read_parameters=read_parameters,
    )


def get_be_spec() -> BeSpec | None:
    return None
