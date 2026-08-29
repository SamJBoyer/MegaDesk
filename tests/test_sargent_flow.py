"""Sargent end to end: type a rough prompt, read the rewrite.

The chain is cut where the money is spent. Tests inject a rewrite callable in
place of the OpenAI HTTP call; everything on both sides of that cut is real —
the canvas and its widget callbacks, the SARGENT:ASK consumer group, and the
SARGENT:ANSWER payloads.

Two seams get their own tests because they fail independently:

* FE ↔ stream, with a canned answer written onto SARGENT:ANSWER.
* BE ↔ model, with the real SargentManager loop and a fake rewrite function.
"""

from __future__ import annotations

import pytest
from conftest import SARGENT_ANSWER_CANONICAL_FIELDS, SARGENT_ASK_CANONICAL_FIELDS
from megadesk_contracts.wire import sargent as wire
from SargentManager.openai_client import OpenAIError

ROUGH = "i need a node that take my prompt and make it beter spelling grammer etc"
REWRITE = (
    "Write a node that takes my prompt and improves it: fix spelling, "
    "grammar, and structure while keeping the original intent."
)


# --- wire (no GUI, no Redis) ----------------------------------------------


def test_ask_writer_emits_only_canonical_fields() -> None:
    fields = wire.ask_fields(session_id="sess-1", prompt_id="p-1", prompt=ROUGH)
    assert set(fields) == set(SARGENT_ASK_CANONICAL_FIELDS)
    assert all(isinstance(v, str) for v in fields.values())


def test_answer_writer_emits_only_canonical_fields() -> None:
    fields = wire.answer_fields(
        session_id="sess-1", prompt_id="p-1", rewrite=REWRITE
    )
    assert set(fields) == set(SARGENT_ANSWER_CANONICAL_FIELDS)
    assert all(isinstance(v, str) for v in fields.values())


def test_ask_and_answer_round_trip_through_the_parsers() -> None:
    ask = wire.parse_ask(wire.ask_fields(session_id="sess-1", prompt_id="p-1", prompt=ROUGH))
    assert ask["prompt"] == ROUGH
    parsed = wire.parse_answer(
        wire.answer_fields(session_id="sess-1", prompt_id="p-1", rewrite=REWRITE)
    )
    assert parsed["rewrite"] == REWRITE
    assert parsed["status"] == wire.STATUS_OK


def test_ask_rejects_an_empty_prompt() -> None:
    with pytest.raises(ValueError, match="prompt"):
        wire.ask_fields(session_id="sess-1", prompt_id="p-1", prompt="   ")


def test_answer_rejects_a_status_outside_the_vocabulary() -> None:
    with pytest.raises(ValueError):
        wire.answer_fields(
            session_id="sess-1",
            prompt_id="p-1",
            rewrite=REWRITE,
            status="almost-done",
        )


# --- FE -------------------------------------------------------------------


@pytest.mark.canvas
@pytest.mark.redis
def test_sending_publishes_a_canonical_ask_and_shows_the_rewrite(
    harness, redis_client, read_stream
) -> None:
    sargent = harness.drop("sargent")
    sargent.type_into("prompt", ROUGH)

    asks = read_stream(wire.ASK_STREAM)
    assert len(asks) == 1
    _entry_id, ask = asks[0]
    assert set(ask) == set(SARGENT_ASK_CANONICAL_FIELDS)
    assert ask["prompt"] == ROUGH
    assert sargent.get("prompt") == "", "the input should clear once the ask is sent"
    assert sargent.get("qa_q_1") == ROUGH
    assert sargent.get("status_text") == "Rewriting…"

    redis_client.xadd(
        wire.ANSWER_STREAM,
        wire.answer_fields(
            session_id=ask["session_id"],
            prompt_id=ask["prompt_id"],
            rewrite=REWRITE,
        ),
    )
    harness.wait_until(
        lambda: sargent.get("qa_a_1") == REWRITE,
        message="the rewrite to reach the chat",
    )
    assert sargent.get("status_text") == "Ready"


@pytest.mark.canvas
@pytest.mark.redis
def test_an_empty_prompt_is_not_published(harness, redis_client, read_stream) -> None:
    sargent = harness.drop("sargent")
    sargent.type_into("prompt", "   ")
    assert read_stream(wire.ASK_STREAM) == []
    assert sargent.get("status_text") == "Idle"


@pytest.mark.canvas
@pytest.mark.redis
def test_a_failed_rewrite_is_surfaced_rather_than_swallowed(
    harness, redis_client, read_stream
) -> None:
    sargent = harness.drop("sargent")
    sargent.type_into("prompt", ROUGH)
    _entry_id, ask = read_stream(wire.ASK_STREAM)[0]
    redis_client.xadd(
        wire.ANSWER_STREAM,
        wire.answer_fields(
            session_id=ask["session_id"],
            prompt_id=ask["prompt_id"],
            rewrite="OPENAI_API_KEY is not set",
            status=wire.STATUS_ERROR,
        ),
    )
    harness.wait_until(
        lambda: sargent.get("status_text") == "Rewrite failed",
        message="the FE to report the failed rewrite",
    )
    assert "OPENAI_API_KEY" in sargent.get("qa_a_1")


# --- BE -------------------------------------------------------------------


@pytest.fixture
def sargent_manager(redis_client):
    from SargentManager.manager import SargentManager

    manager = SargentManager(
        ephemeral=redis_client,
        rewrite=lambda prompt: REWRITE if "spelling" in prompt else prompt,
        group=wire.ASK_GROUP,
        consumer="test-sargent",
    )
    yield manager


def ask_through_manager(redis_client, *, prompt: str) -> tuple[str, str]:
    session_id = wire.new_session_id()
    prompt_id = wire.new_prompt_id()
    redis_client.xadd(
        wire.ASK_STREAM,
        wire.ask_fields(session_id=session_id, prompt_id=prompt_id, prompt=prompt),
    )
    return session_id, prompt_id


@pytest.mark.redis
def test_the_manager_rewrites_an_ask_and_acks_it(
    redis_client, read_stream, sargent_manager
) -> None:
    session_id, prompt_id = ask_through_manager(redis_client, prompt=ROUGH)
    assert sargent_manager.poll_once() == 1

    pending = redis_client.xpending(wire.ASK_STREAM, wire.ASK_GROUP)
    assert pending["pending"] == 0

    answers = [
        fields
        for _entry_id, fields in read_stream(wire.ANSWER_STREAM)
        if fields.get("prompt_id") == prompt_id
    ]
    assert len(answers) == 1
    assert set(answers[0]) == set(SARGENT_ANSWER_CANONICAL_FIELDS)
    assert answers[0]["session_id"] == session_id
    assert answers[0]["rewrite"] == REWRITE
    assert answers[0]["status"] == wire.STATUS_OK


@pytest.mark.redis
def test_a_missing_key_is_an_error_answer_not_a_silent_ack(
    redis_client, read_stream
) -> None:
    from SargentManager.manager import SargentManager

    def boom(_prompt: str) -> str:
        raise OpenAIError("OPENAI_API_KEY is not set")

    manager = SargentManager(
        ephemeral=redis_client,
        rewrite=boom,
        group=wire.ASK_GROUP,
        consumer="test-sargent-error",
    )
    _session_id, prompt_id = ask_through_manager(redis_client, prompt=ROUGH)
    assert manager.poll_once() == 1

    answers = [
        fields
        for _entry_id, fields in read_stream(wire.ANSWER_STREAM)
        if fields.get("prompt_id") == prompt_id
    ]
    assert len(answers) == 1
    assert answers[0]["status"] == wire.STATUS_ERROR
    assert "OPENAI_API_KEY" in answers[0]["rewrite"]
    pending = redis_client.xpending(wire.ASK_STREAM, wire.ASK_GROUP)
    assert pending["pending"] == 0


def test_openai_client_refuses_a_missing_key(monkeypatch) -> None:
    from SargentManager import openai_client

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(OpenAIError, match="OPENAI_API_KEY"):
        openai_client.rewrite_prompt("fix this")


def test_openai_client_reads_the_choice_text(monkeypatch) -> None:
    import json
    import urllib.request
    from SargentManager import openai_client

    class _Response:
        def read(self) -> bytes:
            return json.dumps(
                {"choices": [{"message": {"content": "Better prompt."}}]}
            ).encode("utf-8")

        def __enter__(self) -> "_Response":
            return self

        def __exit__(self, *_exc) -> bool:
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_k: _Response())
    assert openai_client.rewrite_prompt("fix this", api_key="sk-test") == "Better prompt."
