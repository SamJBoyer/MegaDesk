"""Canvas wire format: voice tools write, the live canvas applies.

(STREAM, db0) CANVAS:CMD
  - request_id, action, node, suffix, value

(STREAM, db0) CANVAS:REPLY
  - request_id, status, result

VoiceDeck cannot touch Dear PyGui: the canvas process owns the widgets.
These streams are the same seam the integration harness uses in-process —
list nodes, select one, type into a field, click or pick a control — so an
agent can operate the board the way a user would.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Mapping

from megadesk_contracts.wire._fields import one_of, require, stripped, text_field

CMD_STREAM = "CANVAS:CMD"
REPLY_STREAM = "CANVAS:REPLY"

ACTION_LIST_NODES = "list_nodes"
ACTION_DROP_NODE = "drop_node"
ACTION_SELECT_NODE = "select_node"
ACTION_LIST_WIDGETS = "list_widgets"
ACTION_GET = "get"
ACTION_CLICK = "click"
ACTION_TYPE_INTO = "type_into"
ACTION_SELECT = "select"
ACTION_CHECK = "check"
CMD_ACTIONS = frozenset(
    {
        ACTION_LIST_NODES,
        ACTION_DROP_NODE,
        ACTION_SELECT_NODE,
        ACTION_LIST_WIDGETS,
        ACTION_GET,
        ACTION_CLICK,
        ACTION_TYPE_INTO,
        ACTION_SELECT,
        ACTION_CHECK,
    }
)

STATUS_OK = "ok"
STATUS_ERROR = "error"
REPLY_STATUSES = frozenset({STATUS_OK, STATUS_ERROR})

_NODE_REQUIRED = frozenset(
    {
        ACTION_DROP_NODE,
        ACTION_SELECT_NODE,
        ACTION_LIST_WIDGETS,
        ACTION_GET,
        ACTION_CLICK,
        ACTION_TYPE_INTO,
        ACTION_SELECT,
        ACTION_CHECK,
    }
)
_SUFFIX_REQUIRED = frozenset(
    {
        ACTION_GET,
        ACTION_CLICK,
        ACTION_TYPE_INTO,
        ACTION_SELECT,
        ACTION_CHECK,
    }
)
_VALUE_REQUIRED = frozenset({ACTION_SELECT})


def new_request_id() -> str:
    return uuid.uuid4().hex


def result_field(value: Any = "") -> str:
    """JSON payload. Empty means the reply has no extra body."""
    if value == "" or value is None:
        return ""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return ""
        if text[0] in "{[":
            return text
    return json.dumps(value, separators=(",", ":"), default=str)


def parse_result(value: Any) -> Any:
    text = text_field(value)
    if not text:
        return ""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def command_fields(
    *,
    request_id: str,
    action: str,
    node: str = "",
    suffix: str = "",
    value: str = "",
) -> dict[str, str]:
    """Build a CANVAS:CMD payload. Redis takes strings only."""
    fields = {
        "request_id": stripped(request_id),
        "action": one_of("CANVAS:CMD", "action", stripped(action), CMD_ACTIONS),
        "node": stripped(node),
        "suffix": stripped(suffix),
        "value": text_field(value),
    }
    require("CANVAS:CMD", fields, ("request_id", "action"))
    if fields["action"] in _NODE_REQUIRED:
        require("CANVAS:CMD", fields, ("node",))
    if fields["action"] in _SUFFIX_REQUIRED:
        require("CANVAS:CMD", fields, ("suffix",))
    if fields["action"] in _VALUE_REQUIRED:
        require("CANVAS:CMD", fields, ("value",))
    return fields


def parse_command(fields: Mapping[str, Any]) -> dict[str, str]:
    parsed = {
        "request_id": stripped(fields.get("request_id")),
        "action": stripped(fields.get("action")),
        "node": stripped(fields.get("node")),
        "suffix": stripped(fields.get("suffix")),
        "value": text_field(fields.get("value")),
    }
    require("CANVAS:CMD", parsed, ("request_id", "action"))
    one_of("CANVAS:CMD", "action", parsed["action"], CMD_ACTIONS)
    if parsed["action"] in _NODE_REQUIRED:
        require("CANVAS:CMD", parsed, ("node",))
    if parsed["action"] in _SUFFIX_REQUIRED:
        require("CANVAS:CMD", parsed, ("suffix",))
    if parsed["action"] in _VALUE_REQUIRED:
        require("CANVAS:CMD", parsed, ("value",))
    return parsed


def reply_fields(
    *,
    request_id: str,
    status: str = STATUS_OK,
    result: Any = "",
) -> dict[str, str]:
    fields = {
        "request_id": stripped(request_id),
        "status": one_of(
            "CANVAS:REPLY",
            "status",
            stripped(status) or STATUS_OK,
            REPLY_STATUSES,
        ),
        "result": result_field(result),
    }
    require("CANVAS:REPLY", fields, ("request_id",))
    return fields


def parse_reply(fields: Mapping[str, Any]) -> dict[str, Any]:
    parsed = {
        "request_id": stripped(fields.get("request_id")),
        "status": stripped(fields.get("status")) or STATUS_OK,
        "result": parse_result(fields.get("result")),
    }
    require("CANVAS:REPLY", parsed, ("request_id",))
    one_of("CANVAS:REPLY", "status", parsed["status"], REPLY_STATUSES)
    return parsed
