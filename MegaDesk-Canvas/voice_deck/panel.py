"""VoiceDeck operator panel — collapsible canvas chrome (not a Catalog node)."""

from __future__ import annotations

import logging

import dearpygui.dearpygui as dpg
from megadesk_contracts import SupervisorClient

log = logging.getLogger("megadesk.canvas")

VOICE_DECK_PANEL_TAG = "voice_deck_panel_window"
VOICE_DECK_BODY_TAG = "voice_deck_panel_window::body"
VOICE_DECK_ENDPOINT = "voice_deck"


def ensure_voice_deck_running() -> bool:
    """Launch the voice_deck BE once if Supervisor is up and it is not already alive.

    VoiceDeck keeps its BeSpec identity (``voice_deck``) and is still started
    through Supervisor. Canvas owns the singleton: one LAUNCHREQUEST on boot,
    skipped when that endpoint is already in RUNNINGNODES.
    """
    try:
        client = SupervisorClient()
        if not client.redis_ok():
            log.warning("Skip VoiceDeck BE: Redis not reachable at %s", client.redis_url)
            return False
        if not client.backend_ok():
            log.warning(
                "Skip VoiceDeck BE: Supervisor BE not alive "
                "(canvas should start it on launch; use Supervisor panel Start BE)"
            )
            return False
        already = {
            (entry.get("node_endpoint") or "") for entry in client.list_running()
        }
        if VOICE_DECK_ENDPOINT in already:
            log.info("VoiceDeck BE already alive; skip LAUNCHREQUEST")
            return True
        entry_id = client.launch_node(VOICE_DECK_ENDPOINT, parameters="")
        log.info("LAUNCHREQUEST %s -> %s", VOICE_DECK_ENDPOINT, entry_id)
        return True
    except Exception:
        log.exception("VoiceDeck BE launch failed")
        return False


def shutdown_voice_deck_panel() -> None:
    """Stop the FE worker and send VOICE:CONTROL stop (hot-mic safety)."""
    if not dpg.does_item_exist(VOICE_DECK_BODY_TAG):
        return
    cleanup = dpg.get_item_user_data(VOICE_DECK_BODY_TAG)
    if not callable(cleanup):
        return
    try:
        dpg.set_item_user_data(VOICE_DECK_BODY_TAG, None)
    except Exception:
        pass
    try:
        cleanup()
    except Exception:
        log.exception("VoiceDeck panel shutdown failed")


def build_voice_deck_panel(
    parent: str | None = None,
    *,
    width: int = 960,
    height: int = 140,
) -> object | None:
    """Fill the docked VoiceDeck pane (created by the canvas chrome)."""
    target = parent or VOICE_DECK_BODY_TAG
    if not dpg.does_item_exist(target):
        raise RuntimeError(
            f"VoiceDeck pane {target!r} missing; canvas chrome must create it first"
        )
    shutdown_voice_deck_panel()
    dpg.delete_item(target, children_only=True)

    try:
        from voice_deck_frontend.app import VoiceDeck
    except ImportError:
        log.warning("VoiceDeck FE is not installed; panel is empty")
        return None

    panel = VoiceDeck()
    panel.build_ui(
        target,
        tag_prefix=VOICE_DECK_PANEL_TAG,
        width=width,
        height=height,
    )
    return panel
