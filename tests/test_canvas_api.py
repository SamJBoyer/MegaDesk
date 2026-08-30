"""Canvas voice tools: the same verbs the integration harness uses.

Wire tests stay off the GUI. Canvas tests drop a real FE, then either call
``CanvasApi`` in-process or publish ``CANVAS:CMD`` and pump until the reply
and the widget agree.
"""

from __future__ import annotations

import threading

import pytest
from conftest import CANVAS_CMD_CANONICAL_FIELDS, CANVAS_REPLY_CANONICAL_FIELDS
from megadesk_contracts.wire import canvas as wire

from canvas_tools import (
    TOOL_CLICK_WIDGET,
    TOOL_GET_WIDGET,
    TOOL_LIST_NODES,
    TOOL_LIST_WIDGETS,
    TOOL_SELECT_NODE,
    TOOL_TYPE_INTO,
    handle_click_widget,
    handle_get_widget,
    handle_list_nodes,
    handle_list_widgets,
    handle_select_node,
    handle_type_into,
)

class _Host:
    def __init__(self, redis_client) -> None:
        self.ephemeral = redis_client


def _api(harness):
    api = getattr(harness.engine, "canvas_api", None)
    assert api is not None, "build_canvas did not attach CanvasApi"
    return api


def _call_while_pumping(harness, fn):
    result: dict = {}
    error: list[BaseException] = []

    def run() -> None:
        try:
            result["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - surface in the test thread
            error.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    harness.wait_until(
        lambda: not thread.is_alive(),
        timeout=5.0,
        message="canvas tool handler to finish",
    )
    thread.join(timeout=1.0)
    if error:
        raise error[0]
    return result["value"]


def test_command_writer_emits_only_canonical_fields() -> None:
    fields = wire.command_fields(
        request_id="req-1",
        action=wire.ACTION_TYPE_INTO,
        node="notepad",
        suffix="body",
        value="hello",
    )
    assert set(fields) == set(CANVAS_CMD_CANONICAL_FIELDS)
    assert all(isinstance(item, str) for item in fields.values())
    parsed = wire.parse_command(fields)
    assert parsed["action"] == "type_into"
    assert parsed["value"] == "hello"


def test_reply_writer_emits_only_canonical_fields() -> None:
    fields = wire.reply_fields(
        request_id="req-1", status=wire.STATUS_OK, result={"nodes": []}
    )
    assert set(fields) == set(CANVAS_REPLY_CANONICAL_FIELDS)
    assert all(isinstance(item, str) for item in fields.values())
    parsed = wire.parse_reply(fields)
    assert parsed["status"] == "ok"
    assert parsed["result"] == {"nodes": []}


def test_type_into_keeps_interior_whitespace_and_select_needs_a_value() -> None:
    fields = wire.command_fields(
        request_id="req-2",
        action=wire.ACTION_TYPE_INTO,
        node="notepad",
        suffix="body",
        value="  keep  ",
    )
    assert wire.parse_command(fields)["value"] == "  keep  "
    with pytest.raises(ValueError):
        wire.command_fields(
            request_id="req-3", action=wire.ACTION_SELECT, node="graph_bar", suffix="select"
        )


@pytest.mark.canvas
def test_in_process_api_lists_selects_and_types(harness) -> None:
    api = _api(harness)
    dropped = api.drop_node("notepad")
    harness._sync_drivers()
    harness.pump(2)
    driver = harness.driver_for("notepad")

    names = {item["name"] for item in api.list_nodes()}
    assert {"notepad", "graph_bar", "voice_deck"} <= names

    selected = api.select_node("notepad")
    assert selected["member_id"] == dropped["member_id"]
    assert harness.engine._selected_member_id == driver.member_id

    widgets = api.list_widgets("notepad")
    assert "body" in widgets
    assert "git_url" in widgets

    api.type_into("notepad", "body", "from the canvas api")
    harness.pump(2)
    assert driver.get("body") == "from the canvas api"
    assert api.get("notepad", "body") == "from the canvas api"


@pytest.mark.canvas
def test_in_process_api_clicks_chrome_like_a_user(harness) -> None:
    api = _api(harness)
    deck = harness.voice_deck()
    assert "talk_btn" in api.list_widgets("voice_deck")

    api.click("voice_deck", "talk_btn")
    harness.pump(2)
    assert deck.label("talk_btn") == "stop"


@pytest.mark.canvas
@pytest.mark.redis
def test_redis_command_types_into_a_hosted_field(harness, redis_client) -> None:
    driver = harness.drop("notepad")
    request_id = wire.new_request_id()
    redis_client.xadd(
        wire.CMD_STREAM,
        wire.command_fields(
            request_id=request_id,
            action=wire.ACTION_TYPE_INTO,
            node="notepad",
            suffix="body",
            value="from voice",
        ),
    )
    harness.wait_until(
        lambda: driver.get("body") == "from voice",
        message="notepad body to receive CANVAS:CMD type_into",
    )
    replies = [wire.parse_reply(fields) for _id, fields in redis_client.xrange(wire.REPLY_STREAM)]
    matched = [item for item in replies if item["request_id"] == request_id]
    assert matched and matched[-1]["status"] == "ok"


@pytest.mark.canvas
@pytest.mark.redis
def test_voice_tools_drive_the_board_through_the_router(harness, redis_client) -> None:
    host = _Host(redis_client)
    listed = _call_while_pumping(harness, lambda: handle_list_nodes({}, host))
    assert listed["status"] == "ok"
    chrome = {item["name"] for item in listed["nodes"] if item["kind"] == "chrome"}
    assert {"graph_bar", "voice_deck"} <= chrome

    harness.drop("notepad")
    widgets = _call_while_pumping(
        harness, lambda: handle_list_widgets({"node": "notepad"}, host)
    )
    assert widgets["status"] == "ok"
    assert "body" in widgets["widgets"]

    typed = _call_while_pumping(
        harness,
        lambda: handle_type_into(
            {"node": "notepad", "suffix": "body", "text": "voice typed this"},
            host,
        ),
    )
    assert typed["status"] == "ok"
    assert harness.driver_for("notepad").get("body") == "voice typed this"

    read = _call_while_pumping(
        harness,
        lambda: handle_get_widget({"node": "notepad", "suffix": "body"}, host),
    )
    assert read == {"status": "ok", "value": "voice typed this"}

    selected = _call_while_pumping(
        harness, lambda: handle_select_node({"node": "notepad"}, host)
    )
    assert selected["status"] == "ok"
    assert selected["name"] == "notepad"

    clicked = _call_while_pumping(
        harness,
        lambda: handle_click_widget({"node": "voice_deck", "suffix": "talk_btn"}, host),
    )
    assert clicked["status"] == "ok"
    assert harness.voice_deck().label("talk_btn") == "stop"
