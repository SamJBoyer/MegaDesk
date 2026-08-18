"""CodeScope wire format: questions in, streamed answers out.

(STREAM, db0) CODEQ:ASK
  - session_id, question_id, repo, question, mode

(STREAM, db0) CODEQ:ANSWER
  - session_id, question_id, repo, answer, final, status

(HASH, db1) CODESCOPE:SESSION:<session_id>
  - repo, clone_path, agent_id, model, status

``question_id`` is minted by the asker and is the only join key between the two
streams. One question produces several CODEQ:ANSWER entries as the agent's text
arrives, so a reader matches on ``question_id`` rather than on stream order, and
``final`` marks the last entry it will ever see for that question.
"""

from __future__ import annotations

import uuid
from typing import Any, Mapping

from megadesk_contracts.wire._fields import (
    bool_field,
    is_true,
    one_of,
    require,
    stripped,
    text_field,
)
from megadesk_contracts.wire.factory import DEFAULT_MODEL

ASK_STREAM = "CODEQ:ASK"
ANSWER_STREAM = "CODEQ:ANSWER"
SESSION_PREFIX = "CODESCOPE:SESSION:"
ASK_GROUP = "code_scope"

# What the asker wants back: prose for a human, or a ready-to-dispatch ticket.
MODE_ANSWER = "answer"
MODE_PROPOSE_TICKET = "propose_ticket"
ASK_MODES = frozenset({MODE_ANSWER, MODE_PROPOSE_TICKET})

STATUS_OK = "ok"
STATUS_ERROR = "error"
ANSWER_STATUSES = frozenset({STATUS_OK, STATUS_ERROR})

SESSION_IDLE = "idle"
SESSION_CLONING = "cloning"
SESSION_READY = "ready"
SESSION_THINKING = "thinking"
SESSION_ERROR = "error"
SESSION_STATUSES = frozenset(
    {SESSION_IDLE, SESSION_CLONING, SESSION_READY, SESSION_THINKING, SESSION_ERROR}
)


def session_key(session_id: str) -> str:
    text = stripped(session_id)
    if not text:
        raise ValueError("CODESCOPE:SESSION requires a session_id")
    return f"{SESSION_PREFIX}{text}"


def session_id_from_key(key: str) -> str:
    if not key.startswith(SESSION_PREFIX):
        raise ValueError(f"Key {key!r} is not a {SESSION_PREFIX}* hash")
    session_id = key[len(SESSION_PREFIX) :]
    if not session_id:
        raise ValueError(f"Empty session_id in key {key!r}")
    return session_id


def new_session_id() -> str:
    return uuid.uuid4().hex


def new_question_id() -> str:
    return uuid.uuid4().hex


# --- CODEQ:ASK -------------------------------------------------------------


def ask_fields(
    *,
    session_id: str,
    question_id: str,
    repo: str,
    question: str,
    mode: str = MODE_ANSWER,
) -> dict[str, str]:
    fields = {
        "session_id": stripped(session_id),
        "question_id": stripped(question_id),
        "repo": stripped(repo),
        "question": text_field(question),
        "mode": one_of(
            "CODEQ:ASK", "mode", stripped(mode) or MODE_ANSWER, ASK_MODES
        ),
    }
    require("CODEQ:ASK", fields, ("session_id", "question_id", "repo", "question"))
    return fields


def parse_ask(fields: Mapping[str, Any]) -> dict[str, str]:
    parsed = {
        "session_id": stripped(fields.get("session_id")),
        "question_id": stripped(fields.get("question_id")),
        "repo": stripped(fields.get("repo")),
        "question": text_field(fields.get("question")),
        "mode": stripped(fields.get("mode")) or MODE_ANSWER,
    }
    require("CODEQ:ASK", parsed, ("session_id", "question_id", "repo", "question"))
    one_of("CODEQ:ASK", "mode", parsed["mode"], ASK_MODES)
    return parsed


# --- CODEQ:ANSWER ----------------------------------------------------------


def answer_fields(
    *,
    session_id: str,
    question_id: str,
    repo: str,
    answer: str,
    final: bool = False,
    status: str = STATUS_OK,
) -> dict[str, str]:
    fields = {
        "session_id": stripped(session_id),
        "question_id": stripped(question_id),
        "repo": stripped(repo),
        "answer": text_field(answer),
        "final": bool_field(final),
        "status": one_of(
            "CODEQ:ANSWER", "status", stripped(status) or STATUS_OK, ANSWER_STATUSES
        ),
    }
    require("CODEQ:ANSWER", fields, ("session_id", "question_id", "repo"))
    # An empty answer is only meaningful as a terminator — otherwise a reader
    # would speak silence and never learn the question went nowhere.
    if not fields["answer"].strip() and not is_true(fields["final"]):
        raise ValueError("CODEQ:ANSWER requires answer text unless final is true")
    return fields


def parse_answer(fields: Mapping[str, Any]) -> dict[str, Any]:
    parsed = {
        "session_id": stripped(fields.get("session_id")),
        "question_id": stripped(fields.get("question_id")),
        "repo": stripped(fields.get("repo")),
        "answer": text_field(fields.get("answer")),
        "final": is_true(fields.get("final", False)),
        "status": stripped(fields.get("status")) or STATUS_OK,
    }
    require("CODEQ:ANSWER", parsed, ("session_id", "question_id", "repo"))
    one_of("CODEQ:ANSWER", "status", parsed["status"], ANSWER_STATUSES)
    return parsed


# --- CODESCOPE:SESSION:<session_id> ---------------------------------------


def session_fields(
    *,
    repo: str,
    clone_path: str,
    model: str = DEFAULT_MODEL,
    status: str = SESSION_IDLE,
    agent_id: str = "",
) -> dict[str, str]:
    """Enough to resume: ``agent_id`` lets a restarted BE call ``Agent.resume``."""
    fields = {
        "repo": stripped(repo),
        "clone_path": stripped(clone_path),
        "agent_id": stripped(agent_id),
        "model": stripped(model) or DEFAULT_MODEL,
        "status": one_of(
            "CODESCOPE:SESSION",
            "status",
            stripped(status) or SESSION_IDLE,
            SESSION_STATUSES,
        ),
    }
    require("CODESCOPE:SESSION", fields, ("repo", "clone_path"))
    return fields


def parse_session(fields: Mapping[str, Any]) -> dict[str, str]:
    parsed = {
        "repo": stripped(fields.get("repo")),
        "clone_path": stripped(fields.get("clone_path")),
        "agent_id": stripped(fields.get("agent_id")),
        "model": stripped(fields.get("model")) or DEFAULT_MODEL,
        "status": stripped(fields.get("status")) or SESSION_IDLE,
    }
    require("CODESCOPE:SESSION", parsed, ("repo", "clone_path"))
    one_of("CODESCOPE:SESSION", "status", parsed["status"], SESSION_STATUSES)
    return parsed
