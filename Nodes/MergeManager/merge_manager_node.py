"""MegaDesk.nodes entry point for MergeManager (FE-only)."""

from __future__ import annotations

from pathlib import Path

from megadesk_contracts import BeSpec, FeSpec, Mode

from merge_manager_app import build_ui

_ICON = str(
    Path(__file__).resolve().parent / "Etc" / "Artwork" / "icon.png"
)


def get_exec_spec(mode: Mode) -> FeSpec | BeSpec | None:
    if mode == "FE":
        icon = _ICON if Path(_ICON).is_file() else None
        return FeSpec(
            name="merge_manager",
            description="Resolve FINISHED worktrees into the agents branch.",
            icon=icon,
            default_width=640,
            default_height=220,
            build=build_ui,
        )
    return None
