"""MegaDesk.nodes entry point for CloudDispatcher (FE + BE).

FE: drafts, live runs and PR links (requires ``[canvas]``).
BE: CloudDispatcherManager, which launches Cursor cloud agents and follows them.
"""

from __future__ import annotations

import sys
from pathlib import Path

from megadesk_contracts import BeSpec, FeSpec, Mode

_CLOUD_DISPATCHER_ROOT = Path(__file__).resolve().parent
NODE_NAME = "cloud_dispatcher"
_ICON = str(_CLOUD_DISPATCHER_ROOT / "Etc" / "Artwork" / "icon.png")


def get_fe_spec() -> FeSpec | None:
    from cloud_dispatcher_frontend.app import build_ui

    icon = _ICON if Path(_ICON).is_file() else None
    return FeSpec(
        name=NODE_NAME,
        description="Send a cloud agent to open a PR.",
        icon=icon,
        default_width=520,
        default_height=260,
        build=build_ui,
        backends=(NODE_NAME,),
    )


def get_be_spec() -> BeSpec | None:
    return BeSpec(
        name=NODE_NAME,
        argv=[sys.executable, "-u", "-m", "CloudDispatcherManager"],
        cwd=str(_CLOUD_DISPATCHER_ROOT),
    )


def get_exec_spec(mode: Mode) -> FeSpec | BeSpec | None:
    if mode == "FE":
        return get_fe_spec()
    if mode == "BE":
        return get_be_spec()
    return None
