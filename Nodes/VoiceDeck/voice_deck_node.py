"""MegaDesk.nodes entry point for VoiceDeck (BE only).

The FE is canvas chrome (``voice_deck.panel``), not a Catalog node. The BE
keeps this identity so Supervisor can launch VoiceDeckManager as ``voice_deck``.
"""

from __future__ import annotations

import sys
from pathlib import Path

from megadesk_contracts import BeSpec, FeSpec

_VOICE_DECK_ROOT = Path(__file__).resolve().parent
NODE_NAME = "voice_deck"


def get_fe_spec() -> FeSpec | None:
    return None


def get_be_spec() -> BeSpec | None:
    return BeSpec(
        name=NODE_NAME,
        argv=[sys.executable, "-u", "-m", "VoiceDeckManager"],
        cwd=str(_VOICE_DECK_ROOT),
    )

