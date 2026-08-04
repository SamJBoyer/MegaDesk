"""MegaDesk.nodes entry point for MergeManager (FE-only)."""

from __future__ import annotations

from pathlib import Path

from megadesk import BeSpec, FeSpec, Mode

from app import build_ui

_ICON = str((Path(__file__).resolve().parent / "icon.png"))


def get_exec_spec(mode: Mode) -> FeSpec | BeSpec | None:
    if mode == "FE":
        icon = _ICON if Path(_ICON).is_file() else None
        return FeSpec(
            name="merge_manager",
            description="Resolve FINISHED worktrees into the agents branch.",
            icon=icon,
            default_width=960,
            default_height=600,
            build=build_ui,
        )
    return None
