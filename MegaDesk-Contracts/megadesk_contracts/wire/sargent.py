"""Sargent wire format: a rough prompt in, one rewrite out.

(STREAM, db0) SARGENT:ASK
  - session_id, prompt_id, prompt

(STREAM, db0) SARGENT:ANSWER
  - session_id, prompt_id, rewrite, status

``prompt_id`` is minted by the asker and is the join key. One ask produces one
answer. There is no session hash: the FE only needs to recognize its own
``session_id`` on the answer stream.
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


def new_session_id() -> str:
    return uuid.uuid4().hex


def new_prompt_id() -> str:
    return uuid.uuid4().hex


def ask_fields(*, session_id: str, prompt_id: str, prompt: str) -> dict[str, str]:
    fields = {
        "session_id": stripped(session_id),
        "prompt_id": stripped(prompt_id),
        "prompt": text_field(prompt),
    }
    require("SARGENT:ASK", fields, ("session_id", "prompt_id", "prompt"))
    return fields


def parse_ask(fields: Mapping[str, Any]) -> dict[str, str]:
    parsed = {
        "session_id": stripped(fields.get("session_id")),
        "prompt_id": stripped(fields.get("prompt_id")),
        "prompt": text_field(fields.get("prompt")),
    }
    require("SARGENT:ASK", parsed, ("session_id", "prompt_id", "prompt"))
    return parsed


def answer_fields(
    *,
    session_id: str,
    prompt_id: str,
    rewrite: str,
    status: str = STATUS_OK,
) -> dict[str, str]:
    fields = {
        "session_id": stripped(session_id),
        "prompt_id": stripped(prompt_id),
        "rewrite": text_field(rewrite),
        "status": one_of(
            "SARGENT:ANSWER", "status", stripped(status) or STATUS_OK, ANSWER_STATUSES
        ),
    }
    require("SARGENT:ANSWER", fields, ("session_id", "prompt_id", "rewrite"))
    return fields


def parse_answer(fields: Mapping[str, Any]) -> dict[str, str]:
    parsed = {
        "session_id": stripped(fields.get("session_id")),
        "prompt_id": stripped(fields.get("prompt_id")),
        "rewrite": text_field(fields.get("rewrite")),
        "status": stripped(fields.get("status")) or STATUS_OK,
    }
    require("SARGENT:ANSWER", parsed, ("session_id", "prompt_id", "rewrite"))
    one_of("SARGENT:ANSWER", "status", parsed["status"], ANSWER_STATUSES)
    return parsed
