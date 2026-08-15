"""MegaDesk.nodes entry point for MissionControl (FE + BE).

FE: Dear PyGui MissionControl Floor monitor (requires ``[canvas]``).
BE: MissionControlManager WORKORDER poller / Docker sandbox host.
"""

from __future__ import annotations

import sys
from pathlib import Path

from megadesk_contracts import BeSpec, FeSpec, Mode

_MISSION_CONTROL_ROOT = Path(__file__).resolve().parent
NODE_NAME = "mission_control"
_ICON = str(_MISSION_CONTROL_ROOT / "Etc" / "Artwork" / "icon.png")


def get_fe_spec() -> FeSpec | None:
    from mission_control_frontend.app import build_ui

    icon = _ICON if Path(_ICON).is_file() else None
    return FeSpec(
        name=NODE_NAME,
        description="MissionControl Floor — WORKORDER queue, live sandboxes, Floor repos.",
        icon=icon,
        default_width=520,
        default_height=400,
        build=build_ui,
        backends=(NODE_NAME,),
    )


def get_be_spec() -> BeSpec | None:
    return BeSpec(
        name=NODE_NAME,
        argv=[sys.executable, "-u", "-m", "MissionControlManager"],
        cwd=str(_MISSION_CONTROL_ROOT),
    )


def get_exec_spec(mode: Mode) -> FeSpec | BeSpec | None:
    if mode == "FE":
        return get_fe_spec()
    if mode == "BE":
        return get_be_spec()
    return None
