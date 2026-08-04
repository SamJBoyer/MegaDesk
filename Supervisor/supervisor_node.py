"""MegaDesk.nodes entry point for Supervisor (FE + BE).

FE: Dear PyGui operator panel (requires ``pip install -e .[canvas]``).
BE: commander process lifecycle manager (``python -m commander``).

Dropping the FE on the Executive canvas bootstraps the BE (see
``megadesk.ensure_supervisor_running``). The commander never manages its
own BeSpec via ``launch_node``.
"""

from __future__ import annotations

import sys
from pathlib import Path

from megadesk import BeSpec, FeSpec, Mode

_SUPERVISOR_ROOT = Path(__file__).resolve().parent
NODE_NAME = "supervisor"
_ICON = str(_SUPERVISOR_ROOT / "supervisor_icon.png")


def get_exec_spec(mode: Mode) -> FeSpec | BeSpec | None:
    if mode == "FE":
        from frontend.app import build_ui

        icon = _ICON if Path(_ICON).is_file() else None
        return FeSpec(
            name=NODE_NAME,
            description="Supervisor operator panel (launch / stop BE nodes).",
            icon=icon,
            default_width=480,
            default_height=420,
            build=build_ui,
        )
    if mode == "BE":
        return BeSpec(
            name=NODE_NAME,
            argv=[sys.executable, "-u", "-m", "commander"],
            cwd=str(_SUPERVISOR_ROOT),
        )
    return None
