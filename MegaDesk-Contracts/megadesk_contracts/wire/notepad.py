"""Notepad wire format: voice-tool commands in, the pad applies them.

(STREAM, db0) NOTEPAD:COMMAND
  - action, title, text

``action`` is one of ``create``, ``append``, ``switch``. The notepad FE is the
consumer — VoiceDeck publishes and returns immediately, because writing a note
is a local filesystem change, not a model call.
"""

from __future__ import annotations

from typing import Any, Mapping

from megadesk_contracts.wire._fields import one_of, require, stripped, text_field

COMMAND_STREAM = "NOTEPAD:COMMAND"

ACTION_CREATE = "create"
ACTION_APPEND = "append"
ACTION_SWITCH = "switch"
ACTIONS = frozenset({ACTION_CREATE, ACTION_APPEND, ACTION_SWITCH})

TOOL_NEW_DOCUMENT = "new_document"
TOOL_ADD_TEXT = "add_text"
TOOL_SWITCH_DOCUMENT = "switch_document"


def command_fields(
    *,
    action: str,
    title: str = "",
    text: str = "",
) -> dict[str, str]:
    fields = {
        "action": one_of(
            "NOTEPAD:COMMAND", "action", stripped(action), ACTIONS
        ),
        "title": stripped(title),
        "text": text_field(text),
    }
    if fields["action"] == ACTION_CREATE:
        require("NOTEPAD:COMMAND", fields, ("title",))
    elif fields["action"] == ACTION_APPEND:
        require("NOTEPAD:COMMAND", fields, ("text",))
    elif fields["action"] == ACTION_SWITCH:
        require("NOTEPAD:COMMAND", fields, ("title",))
    return fields


def parse_command(fields: Mapping[str, Any]) -> dict[str, str]:
    parsed = {
        "action": stripped(fields.get("action")),
        "title": stripped(fields.get("title")),
        "text": text_field(fields.get("text")),
    }
    one_of("NOTEPAD:COMMAND", "action", parsed["action"], ACTIONS)
    if parsed["action"] == ACTION_CREATE:
        require("NOTEPAD:COMMAND", parsed, ("title",))
    elif parsed["action"] == ACTION_APPEND:
        require("NOTEPAD:COMMAND", parsed, ("text",))
    elif parsed["action"] == ACTION_SWITCH:
        require("NOTEPAD:COMMAND", parsed, ("title",))
    return parsed


def tool_schemas() -> list[dict]:
    """Realtime ``session.tools`` entries the notepad exposes to VoiceDeck."""
    return [
        {
            "type": "function",
            "name": TOOL_NEW_DOCUMENT,
            "description": (
                "Create a new notepad document and make it the target. "
                "The title becomes the tab name and the .txt file stem."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Document name, shown as a tab.",
                    }
                },
                "required": ["title"],
            },
        },
        {
            "type": "function",
            "name": TOOL_ADD_TEXT,
            "description": (
                "Append text to a notepad document. Defaults to the current "
                "target. Returns immediately."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Text to append.",
                    },
                    "title": {
                        "type": "string",
                        "description": (
                            "Document to write into. Defaults to the current "
                            "target tab."
                        ),
                    },
                },
                "required": ["text"],
            },
        },
        {
            "type": "function",
            "name": TOOL_SWITCH_DOCUMENT,
            "description": "Switch which notepad document is the target tab.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Document name to make current.",
                    }
                },
                "required": ["title"],
            },
        },
    ]
