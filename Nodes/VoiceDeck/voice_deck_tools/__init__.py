"""Session-level voice tools that belong to VoiceDeck itself, not another node.

``end_session`` is the hang-up trap: server VAD already treats a pause as the
end of a turn, and the model will map that onto closing the socket unless it is
told — and the handler enforces — that only an explicit goodbye hangs up.
"""

from __future__ import annotations

import re
from typing import Any

from megadesk_contracts import ToolSpec

NODE_NAME = "voice_deck"
TOOL_END_SESSION = "end_session"

_FAREWELL_RE = re.compile(
    r"\b("
    r"good\s*bye|bye(?:-bye)?"
    r"|that['’]s\s+all|thats\s+all|that\s+is\s+all"
    r"|that['’]s\s+it|thats\s+it"
    r"|hang\s*up"
    r"|end(?:\s+the)?\s+session"
    r"|stop\s+listening"
    r"|i(?:['’]m|\s+am)\s+done"
    r"|we(?:['’]re|\s+are)\s+done"
    r"|i(?:['’]m|\s+am)\s+finished"
    r"|we(?:['’]re|\s+are)\s+finished"
    r")\b",
    re.IGNORECASE,
)

INSTRUCTIONS = f"""You are a voice assistant for a software developer, talking \
about their code out loud.

A pause after the user speaks is the start of your turn, not the end of the \
session. Call {TOOL_END_SESSION} only after the user explicitly says they are \
finished — goodbye, that's all, hang up. Never call it after a question, after \
another tool, or while waiting for a codebase answer.

Keep every reply to one or two spoken sentences. No markdown, no lists, no code \
read aloud character by character. If you do not know, say so."""


def is_farewell(text: str) -> bool:
    """True when the user asked to hang up, not merely finished a turn."""
    return bool(_FAREWELL_RE.search((text or "").strip()))


def handle_end_session(arguments: dict, host: Any) -> dict:
    """Close only on an explicit goodbye, never as a follow-up to a search."""
    if host.pending:
        return {
            "status": "error",
            "detail": (
                "A codebase search is still running. Stay on the line and "
                "wait for the [codebase] message. Do not hang up."
            ),
        }
    if host.last_user_text and not is_farewell(host.last_user_text):
        return {
            "status": "error",
            "detail": (
                "The user did not ask to hang up. Keep listening. "
                f"Call {TOOL_END_SESSION} only after an explicit goodbye."
            ),
        }
    host.stop()
    return {"status": "ok"}


def tool_spec() -> ToolSpec:
    return ToolSpec(
        name=NODE_NAME,
        instructions=INSTRUCTIONS,
        schemas=(
            {
                "type": "function",
                "name": TOOL_END_SESSION,
                "description": (
                    "Hang up only after the user explicitly says they are finished "
                    "(goodbye, that's all, hang up). A pause is not a goodbye. "
                    "Never call this after a question or while waiting for a "
                    "codebase answer."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        ),
        handlers={TOOL_END_SESSION: handle_end_session},
    )
