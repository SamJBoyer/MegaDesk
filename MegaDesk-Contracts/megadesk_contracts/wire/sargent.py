"""Sargent wire format: a rough prompt in, one rewritten prompt out.

(STREAM, db0) SARGENT:ASK
  - prompt_id, prompt

(STREAM, db0) SARGENT:ANSWER
  - prompt_id, rewrite, status

``prompt_id`` is minted by the asker and is the only join key between the two
streams. One ask produces one answer. ``status`` is ``ok`` or ``error``; an
error still carries text so the FE has something to show.
"""

from __future__ import annotations

import uuid
from typing import Any, Mapping

from megadesk_contracts.wire._fields import one_of, require, stripped, text_field

ASK_STREAM = "SARGENT:ASK"
ANSWER_STREAM = "SARGENT:ANSWER"
ASK_GROUP = "sargent"

STATUS_OK = "ok"
STATUS_ERROR = "error"
ANSWER_STATUSES = frozenset({STATUS_OK, STATUS_ERROR})


def new_prompt_id() -> str:
    return uuid.uuid4().hex


# --- SARGENT:ASK -----------------------------------------------------------


def ask_fields(*, prompt_id: str, prompt: str) -> dict[str, str]:
    fields = {
        "prompt_id": stripped(prompt_id),
        "prompt": text_field(prompt),
    }
    require("SARGENT:ASK", fields, ("prompt_id", "prompt"))
    return fields


def parse_ask(fields: Mapping[str, Any]) -> dict[str, str]:
    parsed = {
        "prompt_id": stripped(fields.get("prompt_id")),
        "prompt": text_field(fields.get("prompt")),
    }
    require("SARGENT:ASK", parsed, ("prompt_id", "prompt"))
    return parsed


# --- SARGENT:ANSWER --------------------------------------------------------


def answer_fields(
    *,
    prompt_id: str,
    rewrite: str,
    status: str = STATUS_OK,
) -> dict[str, str]:
    fields = {
        "prompt_id": stripped(prompt_id),
        "rewrite": text_field(rewrite),
        "status": one_of(
            "SARGENT:ANSWER",
            "status",
            stripped(status) or STATUS_OK,
            ANSWER_STATUSES,
        ),
    }
    require("SARGENT:ANSWER", fields, ("prompt_id", "rewrite"))
    return fields


def parse_answer(fields: Mapping[str, Any]) -> dict[str, str]:
    parsed = {
        "prompt_id": stripped(fields.get("prompt_id")),
        "rewrite": text_field(fields.get("rewrite")),
        "status": stripped(fields.get("status")) or STATUS_OK,
    }
    require("SARGENT:ANSWER", parsed, ("prompt_id", "rewrite"))
    one_of("SARGENT:ANSWER", "status", parsed["status"], ANSWER_STATUSES)
    return parsed
