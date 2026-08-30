"""MegaDesk.nodes entry point for PRManager (FE-only)."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Optional

from megadesk_contracts import BeSpec, FeSpec, load_parameter_names, normalize_parameters

from pr_manager_app import DEFAULT_HEIGHT, DEFAULT_WIDTH, build_ui, read_parameters

_ICON = str(
    Path(__file__).resolve().parent / "Etc" / "Artwork" / "icon.png"
)

# Names this node recognizes, declared in parameters.yaml.
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
        name="pr_manager",
        description="Show mergeable PRs, pull them locally, open in VS Code or Cursor.",
        icon=icon,
        default_width=DEFAULT_WIDTH,
        default_height=DEFAULT_HEIGHT,
        build=build,
        parameters=PARAMETERS,
        read_parameters=read_parameters,
    )


def get_be_spec() -> BeSpec | None:
    return None
