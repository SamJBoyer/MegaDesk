"""MegaDesk.nodes entry point for MachineFactory (FE + BE).

FE: Dear PyGui Floor monitor — processed orders, live agents, sandboxes (``[canvas]``).
BE: MachineFactoryManager, which turns orders into local sandboxed agents.
"""

from __future__ import annotations

import sys
from pathlib import Path

from megadesk_contracts import BeSpec, FeSpec, Mode

_MACHINE_FACTORY_ROOT = Path(__file__).resolve().parent
NODE_NAME = "machine_factory"
_ICON = str(_MACHINE_FACTORY_ROOT / "Etc" / "Artwork" / "icon.png")


def get_fe_spec() -> FeSpec | None:
    from machine_factory_frontend.app import build_ui

    icon = _ICON if Path(_ICON).is_file() else None
    return FeSpec(
        name=NODE_NAME,
        description="Run agents in local sandboxes — processed orders, live agents, sandboxes.",
        icon=icon,
        default_width=520,
        default_height=320,
        build=build_ui,
        backends=(NODE_NAME,),
    )


def get_be_spec() -> BeSpec | None:
    return BeSpec(
        name=NODE_NAME,
        argv=[sys.executable, "-u", "-m", "MachineFactoryManager"],
        cwd=str(_MACHINE_FACTORY_ROOT),
    )


def get_exec_spec(mode: Mode) -> FeSpec | BeSpec | None:
    if mode == "FE":
        return get_fe_spec()
    if mode == "BE":
        return get_be_spec()
    return None
