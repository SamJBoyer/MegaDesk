"""MegaDesk.nodes entry point for Plant (BE-only)."""

from __future__ import annotations

import sys
from pathlib import Path

from megadesk import BeSpec, FeSpec, Mode

_PLANT_ROOT = Path(__file__).resolve().parent


def get_exec_spec(mode: Mode) -> FeSpec | BeSpec | None:
    if mode == "BE":
        return BeSpec(
            name="plant",
            argv=[sys.executable, "-u", "-m", "PlantManager"],
            cwd=str(_PLANT_ROOT),
        )
    return None
