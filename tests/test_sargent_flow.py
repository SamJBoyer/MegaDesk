"""Sargent end to end: type a prompt, read the rewrite.

The chain is cut at the OpenAI HTTP call. ``FakeSargent`` stands in for
``OpenAICompleter``; everything on both sides of that cut is real — the canvas
widgets, the SARGENT:ASK consumer group, and the SARGENT:ANSWER payloads.
"""

from __future__ import annotations

import json

import pytest
from conftest import (
    SARGENT_ANSWER_CANONICAL_FIELDS,
    SARGENT_ASK_CANONICAL_FIELDS,
    SARGENT_ASK_GROUP,
)
from megadesk_contracts.wire import sargent as wire

pytestmark = [pytest.mark.canvas, pytest.mark.redis]

ROUGH = "make a node that talks to openai and fixes my grammer"
REWRITE = (
    "Create a MegaDesk node that calls the OpenAI API and corrects the "
    "grammar and structure of the user's prompt."
)


def host_sargent(harness):
    return harness.drop("sargent")


def answers_for(read_stream, prompt_id: str) -> list[dict[str, str]]:
    return [
        fields
        for _entry_id, fields in read_stream(wire.ANSWER_STREAM)
        if fields.get("prompt_id") == prompt_id
    ]


# --- FE --------------------------------------------------------------------


def test_typing_a_prompt_publishes_a_canonical_ask_and_shows_the_rewrite(
    harness, redis_client, read_stream, fake_sargent
) -> None:
    node = host_sargent(harness)
    fake_sargent.add_rewrite("grammer", REWRITE)

    node.type_into("prompt", ROUGH)

    asks = read_stream(wire.ASK_STREAM)
    assert len(asks) == 1
    _entry_id, ask = asks[0]
    assert set(ask) == set(SARGENT_ASK_CANONICAL_FIELDS)
    assert ask["prompt"] == ROUGH
    assert node.get("prompt") == "", "the input should clear once the ask is sent"
    assert node.get("qa_q_1") == ROUGH
    assert node.get("qa_a_1") == "…"

    runs = fake_sargent.run_once()
    assert len(runs) == 1
    assert runs[0].rewrite == REWRITE

    harness.wait_until(
        lambda: node.get("qa_a_1") == REWRITE,
        message="the rewrite to reach the log",
    )
    harness.wait_until(
        lambda: node.get("status_text") == "Idle",
        message="status to settle after the rewrite",
    )
    entries = [fields for _entry_id, fields in read_stream(wire.ANSWER_STREAM)]
    assert len(entries) == 1
    assert set(entries[0]) == set(SARGENT_ANSWER_CANONICAL_FIELDS)


def test_an_empty_prompt_is_not_published(harness, redis_client, read_stream) -> None:
    node = host_sargent(harness)
    node.type_into("prompt", "   ")
    assert read_stream(wire.ASK_STREAM) == []
    assert "qa_q_1" not in node.suffixes()


def test_a_failed_rewrite_is_surfaced_rather_than_swallowed(
    harness, redis_client, fake_sargent
) -> None:
    node = host_sargent(harness)
    fake_sargent.error = "OPENAI_API_KEY is not set"

    node.type_into("prompt", ROUGH)
    fake_sargent.run_once()

    harness.wait_until(
        lambda: node.get("status_text") == "Rewrite failed",
        message="the FE to report the failed rewrite",
    )
    assert "OPENAI_API_KEY" in node.get("qa_a_1")


# --- BE --------------------------------------------------------------------


def test_the_manager_rewrites_and_acks(
    redis_client, read_stream, sargent_manager, fake_sargent
) -> None:
    fake_sargent.add_rewrite("grammer", REWRITE)
    prompt_id = wire.new_prompt_id()
    redis_client.xadd(
        wire.ASK_STREAM, wire.ask_fields(prompt_id=prompt_id, prompt=ROUGH)
    )

    assert sargent_manager.poll_once() == 1

    published = answers_for(read_stream, prompt_id)
    assert len(published) == 1
    assert published[0]["rewrite"] == REWRITE
    assert published[0]["status"] == wire.STATUS_OK
    assert fake_sargent.prompts == [ROUGH]

    pending = redis_client.xpending(wire.ASK_STREAM, SARGENT_ASK_GROUP)
    count = pending.get("pending") if isinstance(pending, dict) else pending[0]
    assert int(count or 0) == 0, "a handled ask must not stay pending"


def test_a_missing_api_key_is_reported_and_the_ask_is_acked(
    redis_client, read_stream
) -> None:
    from SargentManager.completer import OpenAICompleter
    from SargentManager.manager import SargentManager

    manager = SargentManager(
        ephemeral=redis_client,
        completer=OpenAICompleter(api_key=""),
        group=SARGENT_ASK_GROUP,
        consumer="test-sargent-key",
    )
    prompt_id = wire.new_prompt_id()
    redis_client.xadd(
        wire.ASK_STREAM, wire.ask_fields(prompt_id=prompt_id, prompt=ROUGH)
    )
    manager.poll_once()

    published = answers_for(read_stream, prompt_id)
    assert len(published) == 1
    assert published[0]["status"] == wire.STATUS_ERROR
    assert "OPENAI_API_KEY" in published[0]["rewrite"]
    pending = redis_client.xpending(wire.ASK_STREAM, SARGENT_ASK_GROUP)
    count = pending.get("pending") if isinstance(pending, dict) else pending[0]
    assert int(count or 0) == 0


def test_openai_completer_posts_one_chat_completion() -> None:
    from SargentManager.completer import CHAT_URL, OpenAICompleter

    captured: dict[str, object] = {}

    class _Resp:
        def read(self) -> bytes:
            return json.dumps(
                {"choices": [{"message": {"content": REWRITE}}]}
            ).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["auth"] = request.get_header("Authorization")
        return _Resp()

    completer = OpenAICompleter(
        api_key="sk-test", model="gpt-4o", urlopen=fake_urlopen
    )
    assert completer(ROUGH) == REWRITE
    assert captured["url"] == CHAT_URL
    assert captured["auth"] == "Bearer sk-test"
    body = captured["body"]
    assert body["model"] == "gpt-4o"
    assert body["messages"][1]["content"] == ROUGH
    assert "Do not answer" in body["messages"][0]["content"]
