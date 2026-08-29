"""Notepad wire format: voice tools write, the FE applies.

(STREAM, db0) NOTEPAD:CMD
  - action, title, text

The pad itself is a canvas FE. VoiceDeck does not know about tabs or files; it
publishes one of three verbs and the hosted notepad turns that into a document
change. There is no consumer group: every hosted pad XREADs the same entries,
the way CodeScope and VoiceDeck both read CODEQ:ANSWER.
"""

from __future__ import annotations

from typing import Any, Mapping

from megadesk_contracts.wire._fields import one_of, require, stripped, text_field

CMD_STREAM = "NOTEPAD:CMD"

ACTION_CREATE = "create"
ACTION_APPEND = "append"
ACTION_SWITCH = "switch"
CMD_ACTIONS = frozenset({ACTION_CREATE, ACTION_APPEND, ACTION_SWITCH})


def command_fields(
    *,
    action: str,
    title: str = "",
    text: str = "",
) -> dict[str, str]:
    """Build a NOTEPAD:CMD payload. Redis takes strings only."""
    fields = {
        "action": one_of(
            "NOTEPAD:CMD", "action", stripped(action), CMD_ACTIONS
        ),
        "title": stripped(title),
        "text": text_field(text),
    }
    if fields["action"] in {ACTION_CREATE, ACTION_SWITCH}:
        require("NOTEPAD:CMD", fields, ("title",))
    if fields["action"] == ACTION_APPEND:
        require("NOTEPAD:CMD", fields, ("text",))
    return fields


def parse_command(fields: Mapping[str, Any]) -> dict[str, str]:
    parsed = {
        "action": stripped(fields.get("action")),
        "title": stripped(fields.get("title")),
        "text": text_field(fields.get("text")),
    }
    one_of("NOTEPAD:CMD", "action", parsed["action"], CMD_ACTIONS)
    if parsed["action"] in {ACTION_CREATE, ACTION_SWITCH}:
        require("NOTEPAD:CMD", parsed, ("title",))
    if parsed["action"] == ACTION_APPEND:
        require("NOTEPAD:CMD", parsed, ("text",))
    return parsed
