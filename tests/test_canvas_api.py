"""Canvas voice tools: wire fields and VoiceDeck handlers.

Handlers publish ``CANVAS:CMD`` and wait for ``CANVAS:REPLY``. A stand-in
canvas replies so these tests never boot Dear PyGui. In-process typing and
clicking are covered in ``test_canvas_harness.py``.
"""

from __future__ import annotations

import threading
import time

import pytest
from conftest import CANVAS_CMD_CANONICAL_FIELDS, CANVAS_REPLY_CANONICAL_FIELDS
from megadesk_contracts.wire import canvas as wire

from canvas_tools import handle_list_nodes, handle_type_into


class _Host:
    def __init__(self, redis_client) -> None:
        self.ephemeral = redis_client


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
            request_id="req-3",
            action=wire.ACTION_SELECT,
            node="graph_bar",
            suffix="select",
        )


@pytest.mark.redis
def test_drain_commands_returns_immediately_when_cmd_is_empty(redis_client) -> None:
    """Empty CANVAS:CMD must not stall the render loop.

    redis-py ``xread(..., block=0)`` sends Redis ``XREAD BLOCK 0``, which
    waits forever. The live canvas then freezes until socket_timeout (~2s)
    on every frame.
    """
    from engine.canvas_api import CanvasApi

    api = CanvasApi(object())
    try:
        started = time.perf_counter()
        assert api.drain_commands() == 0
        elapsed = time.perf_counter() - started
    finally:
        api.detach()
    assert elapsed < 0.4, (
        f"empty CANVAS:CMD XREAD blocked for {elapsed:.2f}s (BLOCK 0?)"
    )


@pytest.mark.redis
def test_voice_tools_publish_canonical_commands(redis_client) -> None:
    """Handlers write CANVAS:CMD; a stand-in canvas replies so they do not block."""

    def reply_loop(stop: threading.Event) -> None:
        cursor = "0-0"
        while not stop.is_set():
            items = redis_client.xread({wire.CMD_STREAM: cursor}, count=8, block=50)
            for _stream, entries in items or []:
                for entry_id, fields in entries:
                    cursor = str(entry_id)
                    parsed = wire.parse_command(fields)
                    redis_client.xadd(
                        wire.REPLY_STREAM,
                        wire.reply_fields(
                            request_id=parsed["request_id"],
                            status=wire.STATUS_OK,
                            result={"nodes": [{"kind": "chrome", "name": "graph_bar"}]}
                            if parsed["action"] == wire.ACTION_LIST_NODES
                            else {"node": parsed["node"], "suffix": parsed["suffix"]},
                        ),
                    )

    stop = threading.Event()
    thread = threading.Thread(target=reply_loop, args=(stop,), daemon=True)
    thread.start()
    host = _Host(redis_client)
    try:
        listed = handle_list_nodes({}, host)
        assert listed["status"] == "ok"
        assert listed["nodes"][0]["name"] == "graph_bar"

        typed = handle_type_into(
            {"node": "notepad", "suffix": "body", "text": "voice typed this"},
            host,
        )
        assert typed["status"] == "ok"

        commands = [wire.parse_command(fields) for _id, fields in redis_client.xrange(wire.CMD_STREAM)]
        assert [item["action"] for item in commands] == ["list_nodes", "type_into"]
        assert all(set(item) == set(CANVAS_CMD_CANONICAL_FIELDS) for item in commands)
        assert commands[1]["value"] == "voice typed this"
        assert commands[1]["suffix"] == "body"
    finally:
        stop.set()
        thread.join(timeout=1.0)

