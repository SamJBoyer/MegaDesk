"""MegaDesk.nodes entry point for Notepad (FE-only)."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Optional

from megadesk_contracts import BeSpec, FeSpec, ToolSpec

from notepad_frontend.app import build_ui

_ICON = str(Path(__file__).resolve().parent / "Etc" / "Artwork" / "icon.png")

NODE_NAME = "notepad"


def get_fe_spec(
    parameters: Optional[Mapping[str, str]] = None,
) -> FeSpec | None:
    """FE spec for the tabbed notepad. Graph parameters are unused."""
    icon = _ICON if Path(_ICON).is_file() else None

    def build(parent: str, **kwargs: object) -> None:
        build_ui(parent, **kwargs)  # type: ignore[arg-type]

    return FeSpec(
        name=NODE_NAME,
        description="Tabbed notepad; notes are text files a voice agent can write.",
        icon=icon,
        default_width=280,
        default_height=200,
        build=build,
    )


def get_be_spec() -> BeSpec | None:
    return None


def get_tool_spec() -> ToolSpec | None:
    from notepad_tools import tool_spec

    return tool_spec()
