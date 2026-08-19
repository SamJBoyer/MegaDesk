"""MegaDesk.nodes entry point for CloudFactory (FE + BE).

FE: queued CLOUDORDERs and live agents (requires ``[canvas]``).
BE: CloudFactoryManager, which launches Cursor cloud agents and follows them.
"""

from __future__ import annotations

import sys
from pathlib import Path

from megadesk_contracts import BeSpec, FeSpec

_CLOUD_FACTORY_ROOT = Path(__file__).resolve().parent
NODE_NAME = "cloud_factory"
_ICON = str(_CLOUD_FACTORY_ROOT / "Etc" / "Artwork" / "icon.png")


def get_fe_spec() -> FeSpec | None:
    from cloud_factory_frontend.app import build_ui

    icon = _ICON if Path(_ICON).is_file() else None
    return FeSpec(
        name=NODE_NAME,
        description="Run agents in the cloud — queued orders and live agents.",
        icon=icon,
        default_width=420,
        default_height=140,
        build=build_ui,
        backends=(NODE_NAME,),
    )


def get_be_spec() -> BeSpec | None:
    return BeSpec(
        name=NODE_NAME,
        argv=[sys.executable, "-u", "-m", "CloudFactoryManager"],
        cwd=str(_CLOUD_FACTORY_ROOT),
    )

