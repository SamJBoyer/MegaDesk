"""Voice tools that operate the live MegaDesk canvas.

Handlers live here so VoiceDeck can import them without Dear PyGui. They
publish ``CANVAS:CMD`` and wait for the matching ``CANVAS:REPLY`` the canvas
process writes after it fires the real widget callbacks.
"""

from __future__ import annotations

import time
from typing import Any

from megadesk_contracts import ToolSpec
from megadesk_contracts.wire import canvas as wire

NODE_NAME = "canvas"

TOOL_LIST_NODES = "list_nodes"
TOOL_DROP_NODE = "drop_node"
TOOL_SELECT_NODE = "select_node"
TOOL_LIST_WIDGETS = "list_widgets"
TOOL_GET_WIDGET = "get_widget"
TOOL_CLICK_WIDGET = "click_widget"
TOOL_TYPE_INTO = "type_into"
TOOL_SELECT_WIDGET = "select_widget"

REPLY_TIMEOUT_SEC = 3.0

INSTRUCTIONS = f"""The canvas is the MegaDesk board. Use {TOOL_LIST_NODES} to \
see hosted nodes and chrome (graph_bar, voice_deck, catalog, supervisor). \
{TOOL_SELECT_NODE} selects a node the way a click would. {TOOL_LIST_WIDGETS} \
lists a node's widget suffixes. {TOOL_TYPE_INTO} types into a text field and \
submits it. {TOOL_CLICK_WIDGET} presses a button. {TOOL_SELECT_WIDGET} picks \
a combo or listbox value. {TOOL_GET_WIDGET} reads a widget. \
{TOOL_DROP_NODE} places a Catalog node on the board. Address a node by its \
name or member_id, and a widget by the suffix {TOOL_LIST_WIDGETS} returned."""


def _last_id(client: Any, stream: str) -> str:
    try:
        newest = client.xrevrange(stream, count=1)
    except Exception:
        return "0-0"
    if not newest:
        return "0-0"
    return str(newest[0][0])


def _wait_reply(client: Any, request_id: str, cursor: str) -> dict:
    deadline = time.monotonic() + REPLY_TIMEOUT_SEC
    while time.monotonic() < deadline:
        remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
        try:
            reply = client.xread(
                {wire.REPLY_STREAM: cursor},
                count=16,
                block=min(remaining_ms, 200),
            )
        except Exception as exc:
            return {"status": "error", "detail": str(exc)}
        for _stream, items in reply or []:
            for entry_id, fields in items:
                cursor = str(entry_id)
                try:
                    parsed = wire.parse_reply(fields)
                except ValueError:
                    continue
                if parsed["request_id"] != request_id:
                    continue
                if parsed["status"] != wire.STATUS_OK:
                    detail = parsed["result"] or "canvas error"
                    return {"status": "error", "detail": detail}
                result = parsed["result"]
                if result == "" or result is None:
                    return {"status": "ok"}
                if isinstance(result, dict):
                    return {"status": "ok", **result}
                return {"status": "ok", "result": result}
    return {"status": "error", "detail": "canvas did not reply"}


def call_canvas(
    host: Any,
    *,
    action: str,
    node: str = "",
    suffix: str = "",
    value: str = "",
) -> dict:
    """Publish one CANVAS:CMD and wait for the matching reply."""
    request_id = wire.new_request_id()
    client = host.ephemeral
    cursor = _last_id(client, wire.REPLY_STREAM)
    client.xadd(
        wire.CMD_STREAM,
        wire.command_fields(
            request_id=request_id,
            action=action,
            node=node,
            suffix=suffix,
            value=value,
        ),
    )
    return _wait_reply(client, request_id, cursor)


def _node(arguments: dict) -> str:
    return str(arguments.get("node") or "").strip()


def _suffix(arguments: dict) -> str:
    return str(arguments.get("suffix") or "").strip()


def handle_list_nodes(arguments: dict, host: Any) -> dict:
    return call_canvas(host, action=wire.ACTION_LIST_NODES)


def handle_drop_node(arguments: dict, host: Any) -> dict:
    node = _node(arguments)
    if not node:
        return {"status": "error", "detail": "no node was provided"}
    return call_canvas(host, action=wire.ACTION_DROP_NODE, node=node)


def handle_select_node(arguments: dict, host: Any) -> dict:
    node = _node(arguments)
    if not node:
        return {"status": "error", "detail": "no node was provided"}
    return call_canvas(host, action=wire.ACTION_SELECT_NODE, node=node)


def handle_list_widgets(arguments: dict, host: Any) -> dict:
    node = _node(arguments)
    if not node:
        return {"status": "error", "detail": "no node was provided"}
    return call_canvas(host, action=wire.ACTION_LIST_WIDGETS, node=node)


def handle_get_widget(arguments: dict, host: Any) -> dict:
    node = _node(arguments)
    suffix = _suffix(arguments)
    if not node or not suffix:
        return {"status": "error", "detail": "node and suffix are required"}
    return call_canvas(host, action=wire.ACTION_GET, node=node, suffix=suffix)


def handle_click_widget(arguments: dict, host: Any) -> dict:
    node = _node(arguments)
    suffix = _suffix(arguments)
    if not node or not suffix:
        return {"status": "error", "detail": "node and suffix are required"}
    return call_canvas(host, action=wire.ACTION_CLICK, node=node, suffix=suffix)


def handle_type_into(arguments: dict, host: Any) -> dict:
    node = _node(arguments)
    suffix = _suffix(arguments)
    if not node or not suffix:
        return {"status": "error", "detail": "node and suffix are required"}
    text = arguments.get("text")
    if text is None:
        text = arguments.get("value")
    return call_canvas(
        host,
        action=wire.ACTION_TYPE_INTO,
        node=node,
        suffix=suffix,
        value="" if text is None else str(text),
    )


def handle_select_widget(arguments: dict, host: Any) -> dict:
    node = _node(arguments)
    suffix = _suffix(arguments)
    value = str(arguments.get("value") or "")
    if not node or not suffix or not value:
        return {"status": "error", "detail": "node, suffix, and value are required"}
    return call_canvas(
        host, action=wire.ACTION_SELECT, node=node, suffix=suffix, value=value
    )


def tool_spec() -> ToolSpec:
    return ToolSpec(
        name=NODE_NAME,
        instructions=INSTRUCTIONS,
        schemas=(
            {
                "type": "function",
                "name": TOOL_LIST_NODES,
                "description": (
                    "List hosted canvas nodes and chrome panels the voice "
                    "tools can address."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
            {
                "type": "function",
                "name": TOOL_DROP_NODE,
                "description": "Place a Catalog node onto the canvas.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "node": {
                            "type": "string",
                            "description": "Catalog node name, e.g. notepad.",
                        }
                    },
                    "required": ["node"],
                },
            },
            {
                "type": "function",
                "name": TOOL_SELECT_NODE,
                "description": "Select a hosted node the way a click would.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "node": {
                            "type": "string",
                            "description": "Node name or member_id.",
                        }
                    },
                    "required": ["node"],
                },
            },
            {
                "type": "function",
                "name": TOOL_LIST_WIDGETS,
                "description": "List widget suffixes on a node or chrome panel.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "node": {
                            "type": "string",
                            "description": (
                                "Node name, member_id, or chrome "
                                "(graph_bar, voice_deck, catalog, supervisor)."
                            ),
                        }
                    },
                    "required": ["node"],
                },
            },
            {
                "type": "function",
                "name": TOOL_GET_WIDGET,
                "description": "Read the current value of a canvas widget.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "node": {"type": "string"},
                        "suffix": {
                            "type": "string",
                            "description": "Widget suffix from list_widgets.",
                        },
                    },
                    "required": ["node", "suffix"],
                },
            },
            {
                "type": "function",
                "name": TOOL_CLICK_WIDGET,
                "description": "Press a canvas button by firing its real callback.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "node": {"type": "string"},
                        "suffix": {"type": "string"},
                    },
                    "required": ["node", "suffix"],
                },
            },
            {
                "type": "function",
                "name": TOOL_TYPE_INTO,
                "description": (
                    "Type into a text field and submit it, like typing + Enter."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "node": {"type": "string"},
                        "suffix": {"type": "string"},
                        "text": {
                            "type": "string",
                            "description": "Text to put in the field.",
                        },
                    },
                    "required": ["node", "suffix", "text"],
                },
            },
            {
                "type": "function",
                "name": TOOL_SELECT_WIDGET,
                "description": "Pick a combo or listbox value on the canvas.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "node": {"type": "string"},
                        "suffix": {"type": "string"},
                        "value": {
                            "type": "string",
                            "description": "Option the user would pick.",
                        },
                    },
                    "required": ["node", "suffix", "value"],
                },
            },
        ),
        handlers={
            TOOL_LIST_NODES: handle_list_nodes,
            TOOL_DROP_NODE: handle_drop_node,
            TOOL_SELECT_NODE: handle_select_node,
            TOOL_LIST_WIDGETS: handle_list_widgets,
            TOOL_GET_WIDGET: handle_get_widget,
            TOOL_CLICK_WIDGET: handle_click_widget,
            TOOL_TYPE_INTO: handle_type_into,
            TOOL_SELECT_WIDGET: handle_select_widget,
        },
    )
