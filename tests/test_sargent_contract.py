"""Sargent wire format: one ask, one rewrite, no GUI and no OpenAI."""

from __future__ import annotations

import pytest
from conftest import SARGENT_ANSWER_CANONICAL_FIELDS, SARGENT_ASK_CANONICAL_FIELDS
from megadesk_contracts.wire import sargent as wire

ASK_SAMPLE = {
    "prompt_id": "p-1",
    "prompt": "make a node that talks to openai and fixes my grammer",
}


def test_ask_writer_emits_only_canonical_fields() -> None:
    fields = wire.ask_fields(**ASK_SAMPLE)
    assert set(fields) == set(SARGENT_ASK_CANONICAL_FIELDS)
    assert all(isinstance(v, str) for v in fields.values()), "Redis takes strings only"


def test_answer_writer_emits_only_canonical_fields() -> None:
    fields = wire.answer_fields(
        prompt_id="p-1",
        rewrite="Create a node that talks to OpenAI and fixes my grammar.",
    )
    assert set(fields) == set(SARGENT_ANSWER_CANONICAL_FIELDS)
    assert all(isinstance(v, str) for v in fields.values())


def test_ask_and_answer_round_trip() -> None:
    parsed_ask = wire.parse_ask(wire.ask_fields(**ASK_SAMPLE))
    assert parsed_ask["prompt"] == ASK_SAMPLE["prompt"]
    parsed = wire.parse_answer(
        wire.answer_fields(prompt_id="p-1", rewrite="Clearer prompt.")
    )
    assert parsed["rewrite"] == "Clearer prompt."
    assert parsed["status"] == wire.STATUS_OK


def test_ask_rejects_an_empty_prompt() -> None:
    with pytest.raises(ValueError, match="prompt"):
        wire.ask_fields(prompt_id="p-1", prompt="   ")


def test_answer_rejects_a_status_outside_the_vocabulary() -> None:
    with pytest.raises(ValueError):
        wire.answer_fields(prompt_id="p-1", rewrite="ok", status="almost")


def test_ask_group_is_the_node_name() -> None:
    assert wire.ASK_GROUP == "sargent"
    assert wire.ASK_STREAM == "SARGENT:ASK"
    assert wire.ANSWER_STREAM == "SARGENT:ANSWER"
