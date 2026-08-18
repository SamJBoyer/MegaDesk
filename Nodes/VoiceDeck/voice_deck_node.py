"""MegaDesk.nodes entry point for VoiceDeck (FE + BE).

FE: push-to-talk, state, and a rolling transcript (requires ``[canvas]``).
BE: VoiceDeckManager, which owns the microphone, the speaker, and the realtime
socket, and routes the model's tool calls to CodeScope and CloudFactory.
"""

from __future__ import annotations

import sys
from pathlib import Path

from megadesk_contracts import BeSpec, FeSpec, Mode

_VOICE_DECK_ROOT = Path(__file__).resolve().parent
NODE_NAME = "voice_deck"
_ICON = str(_VOICE_DECK_ROOT / "Etc" / "Artwork" / "icon.png")


def get_fe_spec() -> FeSpec | None:
    from voice_deck_frontend.app import build_ui

    icon = _ICON if Path(_ICON).is_file() else None
    return FeSpec(
        name=NODE_NAME,
        description="Talk to your codebase.",
        icon=icon,
        default_width=460,
        default_height=200,
        build=build_ui,
        backends=(NODE_NAME,),
    )


def get_be_spec() -> BeSpec | None:
    return BeSpec(
        name=NODE_NAME,
        argv=[sys.executable, "-u", "-m", "VoiceDeckManager"],
        cwd=str(_VOICE_DECK_ROOT),
    )


def get_exec_spec(mode: Mode) -> FeSpec | BeSpec | None:
    if mode == "FE":
        return get_fe_spec()
    if mode == "BE":
        return get_be_spec()
    return None
