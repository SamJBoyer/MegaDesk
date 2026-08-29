"""Voice tools for Notepad: create a document, add text, switch the target."""

from __future__ import annotations

from typing import Any

from megadesk_contracts import ToolSpec
from megadesk_contracts.wire import notepad as wire

NODE_NAME = "notepad"
TOOL_CREATE_NOTE = "create_note"
TOOL_ADD_NOTE_TEXT = "add_note_text"
TOOL_SWITCH_NOTE = "switch_note"

INSTRUCTIONS = f"""When the user asks you to write something down, use \
{TOOL_CREATE_NOTE}, {TOOL_ADD_NOTE_TEXT}, and {TOOL_SWITCH_NOTE}. Those tools \
update the notepad on the canvas. They return immediately."""


def handle_create_note(arguments: dict, host: Any) -> dict:
    title = str(arguments.get("title") or "").strip()
    if not title:
        return {"status": "error", "detail": "no title was provided"}
    text = str(arguments.get("text") or "")
    host.ephemeral.xadd(
        wire.CMD_STREAM,
        wire.command_fields(action=wire.ACTION_CREATE, title=title, text=text),
    )
    return {"status": "ok", "title": title}


def handle_add_note_text(arguments: dict, host: Any) -> dict:
    text = str(arguments.get("text") or "")
    if not str(text).strip():
        return {"status": "error", "detail": "no text was provided"}
    title = str(arguments.get("title") or "").strip()
    host.ephemeral.xadd(
        wire.CMD_STREAM,
        wire.command_fields(action=wire.ACTION_APPEND, title=title, text=text),
    )
    return {"status": "ok", "title": title or "current"}


def handle_switch_note(arguments: dict, host: Any) -> dict:
    title = str(arguments.get("title") or "").strip()
    if not title:
        return {"status": "error", "detail": "no title was provided"}
    host.ephemeral.xadd(
        wire.CMD_STREAM,
        wire.command_fields(action=wire.ACTION_SWITCH, title=title),
    )
    return {"status": "ok", "title": title}


def tool_spec() -> ToolSpec:
    return ToolSpec(
        name=NODE_NAME,
        instructions=INSTRUCTIONS,
        schemas=(
            {
                "type": "function",
                "name": TOOL_CREATE_NOTE,
                "description": (
                    "Create a notepad document and make it the current target. "
                    "Optional text becomes the starting body."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": (
                                "Document name, used as the tab and the .txt file."
                            ),
                        },
                        "text": {
                            "type": "string",
                            "description": "Optional starting text.",
                        },
                    },
                    "required": ["title"],
                },
            },
            {
                "type": "function",
                "name": TOOL_ADD_NOTE_TEXT,
                "description": (
                    "Append text to a notepad document. Omitting title writes "
                    "to the current target."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": "Text to add.",
                        },
                        "title": {
                            "type": "string",
                            "description": (
                                "Document to write to. Defaults to the current one."
                            ),
                        },
                    },
                    "required": ["text"],
                },
            },
            {
                "type": "function",
                "name": TOOL_SWITCH_NOTE,
                "description": (
                    "Switch which notepad document later additions go to."
                ),
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
        ),
        handlers={
            TOOL_CREATE_NOTE: handle_create_note,
            TOOL_ADD_NOTE_TEXT: handle_add_note_text,
            TOOL_SWITCH_NOTE: handle_switch_note,
        },
    )
