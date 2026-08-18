"""The voice chain's wire format, independent of any GUI, Redis, or audio device.

CodeScope, VoiceDeck and CloudFactory define their streams once, in
``megadesk_contracts.wire``, so there is no second copy to drift. That leaves one
thing worth pinning here: writers emit exactly the canonical field names, and
builders reject payloads a consumer could not act on.

These tests touch no GUI, no Redis and no microphone, so they run anywhere:
``pytest tests/test_voice_contract.py``.
"""

from __future__ import annotations

import pytest
from conftest import (
    CLOUDFINISHED_CANONICAL_FIELDS,
    CLOUDORDER_CANONICAL_FIELDS,
    CLOUDRUN_CANONICAL_FIELDS,
    CODEQ_ANSWER_CANONICAL_FIELDS,
    CODEQ_ASK_CANONICAL_FIELDS,
    CODEQ_ASK_GROUP,
    CODESCOPE_SESSION_CANONICAL_FIELDS,
    CLOUDORDER_GROUP,
    VOICE_CONTROL_CANONICAL_FIELDS,
    VOICE_EVENT_CANONICAL_FIELDS,
)
from megadesk_contracts.wire import cloud, code_scope, voice

ASK_SAMPLE = {
    "session_id": "sess-1",
    "question_id": "q-1",
    "repo": "widgets",
    "question": "Why does the frame pump need a reset?",
}
ORDER_SAMPLE = {
    "order_id": "order-1",
    "repo_url": "https://github.com/acme/widgets",
    "title": "Document the frame pump",
    "instructions": "Explain why reset() exists in the module docstring.",
    "model": "composer-2.5",
}


# --- canonical field sets --------------------------------------------------


def test_ask_writer_emits_only_canonical_fields() -> None:
    fields = code_scope.ask_fields(**ASK_SAMPLE)
    assert set(fields) == set(CODEQ_ASK_CANONICAL_FIELDS)
    assert all(isinstance(v, str) for v in fields.values()), "Redis takes strings only"


def test_answer_writer_emits_only_canonical_fields() -> None:
    fields = code_scope.answer_fields(
        session_id="sess-1",
        question_id="q-1",
        repo="widgets",
        answer="Because the pump outlives the DPG context.",
        final=True,
    )
    assert set(fields) == set(CODEQ_ANSWER_CANONICAL_FIELDS)
    assert all(isinstance(v, str) for v in fields.values())


def test_session_hash_carries_what_a_restart_needs() -> None:
    fields = code_scope.session_fields(
        repo="widgets",
        clone_path=r"C:\Scope\widgets",
        agent_id="agent-abc",
        model="composer-2.5",
        status=code_scope.SESSION_READY,
    )
    assert set(fields) == set(CODESCOPE_SESSION_CANONICAL_FIELDS)
    # agent_id is the whole point: without it a restarted BE cannot resume the
    # conversation and every question starts from a cold agent.
    assert fields["agent_id"] == "agent-abc"


def test_voice_writers_emit_only_canonical_fields() -> None:
    control = voice.control_fields(action=voice.ACTION_TARGET, value="widgets")
    assert set(control) == set(VOICE_CONTROL_CANONICAL_FIELDS)

    event = voice.event_fields(
        kind=voice.KIND_FINAL, text="why does the pump need a reset", session_id="sess-1"
    )
    assert set(event) == set(VOICE_EVENT_CANONICAL_FIELDS)
    assert all(isinstance(v, str) for v in {**control, **event}.values())


def test_cloud_writers_emit_only_canonical_fields() -> None:
    order = cloud.cloudorder_fields(**ORDER_SAMPLE)
    assert set(order) == set(CLOUDORDER_CANONICAL_FIELDS)

    finished = cloud.cloudfinished_fields(
        order_id="order-1",
        agent_id="bc-abc123",
        status=cloud.STATUS_FINISHED,
        pr_url="https://github.com/acme/widgets/pull/7",
    )
    assert set(finished) == set(CLOUDFINISHED_CANONICAL_FIELDS)

    run = cloud.cloudrun_fields(
        order_id="order-1",
        repo_url=ORDER_SAMPLE["repo_url"],
        title=ORDER_SAMPLE["title"],
        status=cloud.STATUS_RUNNING,
    )
    assert set(run) == set(CLOUDRUN_CANONICAL_FIELDS)
    assert all(isinstance(v, str) for v in {**order, **finished, **run}.values())


def test_consumer_groups_match_the_names_tests_and_docs_use() -> None:
    assert code_scope.ASK_GROUP == CODEQ_ASK_GROUP
    assert cloud.CLOUDORDER_GROUP == CLOUDORDER_GROUP


# --- round trips -----------------------------------------------------------


def test_ask_round_trips_through_the_parser() -> None:
    parsed = code_scope.parse_ask(code_scope.ask_fields(**ASK_SAMPLE))
    assert parsed["question"] == ASK_SAMPLE["question"]
    assert parsed["mode"] == code_scope.MODE_ANSWER


def test_answer_round_trip_restores_the_final_flag_as_a_bool() -> None:
    partial = code_scope.parse_answer(
        code_scope.answer_fields(
            session_id="s", question_id="q", repo="widgets", answer="First sentence."
        )
    )
    last = code_scope.parse_answer(
        code_scope.answer_fields(
            session_id="s",
            question_id="q",
            repo="widgets",
            answer="Last sentence.",
            final=True,
        )
    )
    assert partial["final"] is False
    assert last["final"] is True


def test_cloudorder_round_trip_restores_auto_pr_as_a_bool() -> None:
    parsed = cloud.parse_cloudorder(cloud.cloudorder_fields(**ORDER_SAMPLE))
    assert parsed["auto_pr"] is True
    assert parsed["ref"] == ""
    assert parsed["title"] == ORDER_SAMPLE["title"]


def test_keys_round_trip_with_their_identifiers() -> None:
    assert code_scope.session_id_from_key(code_scope.session_key("sess-1")) == "sess-1"
    assert cloud.agent_id_from_key(cloud.cloudrun_key("bc-abc")) == "bc-abc"


def test_cloud_agent_ids_are_recognized_by_their_prefix() -> None:
    """Cursor routes ``bc-`` to the cloud API; anything else is a local agent."""
    assert cloud.is_cloud_agent_id("bc-abc123") is True
    assert cloud.is_cloud_agent_id("local-abc123") is False


def test_booleans_use_the_same_wire_form_as_the_machine_factory_family(
    machine_wire,
) -> None:
    """One Redis, one encoding: ``"true"`` / ``"false"`` across both stream families.

    Asserted through the two order writers rather than the shared helper, because
    the risk is a writer formatting its own flag, not the helper changing.
    """
    from megadesk_contracts import wire

    machine_order = machine_wire.workorder_fields(
        repo="widgets",
        url="",
        new_wt=True,
        ticket_name="add-widget-tests",
        instructions="Cover the widget module with tests.",
    )
    cloud_order = cloud.cloudorder_fields(
        order_id=cloud.new_order_id(),
        repo_url="https://github.com/acme/widgets",
        title="Document the frame pump reset",
        instructions="Explain why the frame pump needs a reset.",
        auto_pr=True,
    )
    assert machine_order["new_wt"] == cloud_order["auto_pr"] == wire.BOOL_TRUE


# --- rejections ------------------------------------------------------------


def test_ask_requires_a_question() -> None:
    with pytest.raises(ValueError):
        code_scope.ask_fields(
            session_id="s", question_id="q", repo="widgets", question="   "
        )


def test_ask_rejects_an_unknown_mode() -> None:
    with pytest.raises(ValueError):
        code_scope.ask_fields(**ASK_SAMPLE, mode="freestyle")


def test_empty_answer_is_only_allowed_as_a_terminator() -> None:
    """Otherwise a reader speaks silence and never learns the question died."""
    with pytest.raises(ValueError):
        code_scope.answer_fields(
            session_id="s", question_id="q", repo="widgets", answer=""
        )
    terminator = code_scope.answer_fields(
        session_id="s", question_id="q", repo="widgets", answer="", final=True
    )
    assert terminator["final"] == "true"


def test_voice_rejects_unknown_actions_kinds_and_states() -> None:
    with pytest.raises(ValueError):
        voice.control_fields(action="reboot")
    with pytest.raises(ValueError):
        voice.event_fields(kind="mumble", text="hello")
    with pytest.raises(ValueError):
        voice.event_fields(kind=voice.KIND_STATE, text="rebooting")


def test_cloudorder_requires_instructions_and_a_repo_url() -> None:
    with pytest.raises(ValueError):
        cloud.cloudorder_fields(
            order_id="order-1", repo_url="", title="Docs", instructions="Write docs"
        )
    with pytest.raises(ValueError):
        cloud.cloudorder_fields(
            order_id="order-1",
            repo_url=ORDER_SAMPLE["repo_url"],
            title="Docs",
            instructions="",
        )


def test_only_a_startup_failure_may_report_no_agent_id() -> None:
    """A run that executed always has an id; one that never started cannot."""
    with pytest.raises(ValueError):
        cloud.cloudfinished_fields(
            order_id="order-1", agent_id="", status=cloud.STATUS_ERROR
        )
    fields = cloud.cloudfinished_fields(
        order_id="order-1", agent_id="", status=cloud.STATUS_STARTUP_ERROR
    )
    assert fields["agent_id"] == ""


def test_cloudfinished_rejects_a_non_terminal_status() -> None:
    with pytest.raises(ValueError):
        cloud.cloudfinished_fields(
            order_id="order-1", agent_id="bc-abc", status=cloud.STATUS_RUNNING
        )


def test_startup_error_carries_its_retry_advice() -> None:
    """Blind retries of a cloud launch can duplicate runs, so the advice matters."""
    from megadesk_contracts import AgentError, AgentRunError, AgentStartupError

    assert AgentStartupError("401 from the API").retryable is False
    assert AgentStartupError("rate limited", retryable=True).retryable is True
    # One vocabulary for both halves of the distinction, catchable together.
    assert issubclass(AgentStartupError, AgentError)
    assert issubclass(AgentRunError, AgentError)
