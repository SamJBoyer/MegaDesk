"""MegaDesk.nodes entry point for Plant (FE + BE).

FE: Dear PyGui Plant Floor monitor (requires ``pip install -e .[canvas]``).
BE: PlantManager WORKORDER poller / Docker sandbox host.
"""

from __future__ import annotations

import sys
from pathlib import Path

from megadesk import BeSpec, FeSpec, Mode

_PLANT_ROOT = Path(__file__).resolve().parent
NODE_NAME = "plant"
_ICON = str(_PLANT_ROOT / "Etc" / "Artwork" / "icon.png")


def get_exec_spec(mode: Mode) -> FeSpec | BeSpec | None:
    if mode == "FE":
        from frontend.app import build_ui

        icon = _ICON if Path(_ICON).is_file() else None
        return FeSpec(
            name=NODE_NAME,
            description="Plant Floor — WORKORDER queue, live sandboxes, Floor repos.",
            icon=icon,
            default_width=720,
            default_height=640,
            build=build_ui,
        )
    if mode == "BE":
        return BeSpec(
            name=NODE_NAME,
            argv=[sys.executable, "-u", "-m", "PlantManager"],
            cwd=str(_PLANT_ROOT),
        )
    return None
