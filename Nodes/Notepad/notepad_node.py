"""MegaDesk.nodes entry point for Notepad (FE-only)."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Optional

from megadesk_contracts import BeSpec, FeSpec, load_parameter_names, normalize_parameters

from notepad_frontend.app import build_ui, read_parameters

_ICON = str(Path(__file__).resolve().parent / "Etc" / "Artwork" / "icon.png")

# Names this node recognizes, declared in parameters.yaml: GIT_URL.
PARAMETERS = load_parameter_names(__file__)

NODE_NAME = "notepad"


def get_fe_spec(
    parameters: Optional[Mapping[str, str]] = None,
) -> FeSpec | None:
    """FE spec, seeded with the notes repo a graph saved for this member."""
    icon = _ICON if Path(_ICON).is_file() else None
    values = normalize_parameters(parameters, PARAMETERS)

    def build(parent: str, **kwargs: object) -> None:
        build_ui(parent, parameters=values, **kwargs)  # type: ignore[arg-type]

    return FeSpec(
        name=NODE_NAME,
        description="Tabbed notepad; notes save as git-included .txt files.",
        icon=icon,
        default_width=300,
        default_height=220,
        build=build,
        parameters=PARAMETERS,
        read_parameters=read_parameters,
    )


def get_be_spec() -> BeSpec | None:
    return None
