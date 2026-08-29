"""MegaDesk.nodes entry point for Sargent (FE + BE).

FE: Dear PyGui two-column prompt / rewrite (requires ``[canvas]``).
BE: SargentManager, which rewrites SARGENT:ASK with one OpenAI call.
"""

from __future__ import annotations

import sys
from pathlib import Path

from megadesk_contracts import BeSpec, FeSpec

_SARGENT_ROOT = Path(__file__).resolve().parent
NODE_NAME = "sargent"


def get_fe_spec() -> FeSpec | None:
    from sargent_frontend.app import build_ui

    return FeSpec(
        name=NODE_NAME,
        description="Rewrite a rough prompt into a clearer one.",
        icon=None,
        default_width=560,
        default_height=280,
        build=build_ui,
        backends=(NODE_NAME,),
    )


def get_be_spec() -> BeSpec | None:
    return BeSpec(
        name=NODE_NAME,
        argv=[sys.executable, "-u", "-m", "SargentManager"],
        cwd=str(_SARGENT_ROOT),
    )
