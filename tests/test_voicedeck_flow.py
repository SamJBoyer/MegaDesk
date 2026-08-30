"""VoiceDeck without a microphone: control plane, tool router, answer relay.

The cut is the socket. ``FakeRealtime`` scripts what the model says and asks for;
everything else is the production article — the real tool router, the real
CodeScope HTTP client (faked), and the real injection that keeps a thirty-second
code question from stalling a sub-second voice loop.

The two halves are tested apart because they fail apart: the FE is a control
surface that must publish canonical VOICE:CONTROL and render VOICE:EVENT, and the
BE is a router that must never block on an answer it cannot have yet.
"""

from __future__ import annotations

import pytest
from conftest import (
    CLOUDORDER_CANONICAL_FIELDS,
    SARGENT_ASK_CANONICAL_FIELDS,
    VOICE_CONTROL_CANONICAL_FIELDS,
    VOICE_EVENT_CANONICAL_FIELDS,
    WORKORDER_CANONICAL_FIELDS,
)
from megadesk_contracts.testing import RealtimeEvent
from megadesk_contracts.wire import cloud as cloud_wire
from megadesk_contracts.wire import code_scope as scope_wire
from megadesk_contracts.wire import machine as machine_wire
from megadesk_contracts.wire import voice as wire
from megadesk_contracts.wire import sargent as sargent_wire
from canvas_tools import (
    TOOL_CLICK_WIDGET,
    TOOL_LIST_NODES,
    TOOL_SELECT_NODE,
    TOOL_TYPE_INTO,
)
from notepad_tools import TOOL_ADD_NOTE_TEXT, TOOL_CREATE_NOTE, TOOL_SWITCH_NOTE
from promptimprover_tools import TOOL_REVISE_MY_PROMPT
from VoiceDeckManager.tools import (
    ANSWER_PREFIX,
    INSTRUCTIONS,
    REWRITE_PREFIX,
    TOOL_ASK_CODEBASE,
    TOOL_CHOOSE_TICKET,
    TOOL_DISPATCH_DOC_AGENT,
    TOOL_END_SESSION,
    TOOL_LIST_TICKETS,
    TOOL_SEND_TICKET,
    TOOL_SET_DISPATCH,
    TOOL_SET_REPO,
    is_farewell,
    tool_schemas,
)

pytestmark = pytest.mark.redis

QUESTION = "Why does the frame pump need a reset?"
ANSWER = "Because the pump outlives the Dear PyGui context."


# --- helpers ---------------------------------------------------------------


def seed_scope_session(
    fake_codescope, *, repo: str = "widgets", url: str = "https://github.com/acme/widgets"
) -> str:
    """Pretend CodeScope has a repo loaded, since VoiceDeck asks whoever does."""
    return fake_codescope.seed_repo(repo=repo, url=url)["session_id"]


def queue_answer(
    fake_codescope,
    *,
    session_id: str,
    question_id: str,
    repo: str = "widgets",
    answer: str = ANSWER,
    final: bool = True,
    status: str = scope_wire.STATUS_OK,
) -> None:
    """Answer as CodeScope would, on the HTTP stream VoiceDeck is watching."""
    fake_codescope.queue_answer(
        question_id,
        [
            scope_wire.parse_answer(
                scope_wire.answer_fields(
                    session_id=session_id,
                    question_id=question_id,
                    repo=repo,
                    answer=answer,
                    final=final,
                    status=status,
                )
            )
        ],
    )


def ask_for(voice_session, fake_realtime, question: str = QUESTION) -> tuple[str, str]:
    """Drive one ``ask_codebase`` turn. Returns ``(call_id, question_id)``."""
    call_id = fake_realtime.call_tool(TOOL_ASK_CODEBASE, {"question": question})
    voice_session.pump_events()
    (question_id,) = list(voice_session._pending)
    return call_id, question_id


def events_of(read_stream, kind: str) -> list[str]:
    return [
        fields["text"]
        for _entry_id, fields in read_stream(wire.EVENT_STREAM)
        if fields["kind"] == kind
    ]


def controls(read_stream) -> list[tuple[str, str]]:
    return [
        (fields["action"], fields["value"])
        for _entry_id, fields in read_stream(wire.CONTROL_STREAM)
    ]


# --- the timing problem ----------------------------------------------------


def test_asking_returns_searching_immediately_and_queues_the_ask(
    voice_session, fake_realtime, fake_codescope, redis_client, read_stream
) -> None:
    """The whole design rests on this: the tool result is not the answer.

    Waiting for the agent here would leave the model holding a dead line for
    thirty seconds, which sounds exactly like a crash.
    """
    scope_id = seed_scope_session(fake_codescope)
    voice_session.start()

    call_id, question_id = ask_for(voice_session, fake_realtime)

    result = fake_realtime.result_for(call_id)
    assert result["status"] == "searching"
    assert ANSWER_PREFIX in result["detail"], "the model must be told where to look"
    assert TOOL_END_SESSION in result["detail"], "waiting is not a hang-up"

    assert voice_session._scope_asks == [
        (scope_id, QUESTION, question_id, scope_wire.MODE_ANSWER)
    ]
    assert fake_codescope.asks == [], "HTTP is not called until the answer pump"
    assert fake_realtime.injected == [], "nothing can be spoken before an answer exists"


def test_an_answer_is_injected_for_the_model_to_speak(
    voice_session, fake_realtime, fake_codescope, redis_client, read_stream
) -> None:
    scope_id = seed_scope_session(fake_codescope)
    voice_session.start()
    _call_id, question_id = ask_for(voice_session, fake_realtime)
    queue_answer(
        fake_codescope, session_id=scope_id, question_id=question_id, answer=ANSWER
    )
    assert voice_session.pump_answers() == 1

    assert fake_realtime.injected == [f"{ANSWER_PREFIX} {ANSWER}"]
    assert voice_session.state == wire.STATE_SPEAKING

    voice_session.pump_events()
    assert events_of(read_stream, wire.KIND_ANSWER) == [ANSWER], (
        "the marker is an instruction to the model, not something the user reads"
    )


def test_streamed_sentences_are_injected_as_they_arrive(
    voice_session, fake_realtime, fake_codescope
) -> None:
    """Waiting for ``final`` would trade the whole point of streaming for tidiness."""
    scope_id = seed_scope_session(fake_codescope)
    voice_session.start()
    _call_id, question_id = ask_for(voice_session, fake_realtime)
    fake_codescope.queue_answer(
        question_id,
        [
            scope_wire.parse_answer(
                scope_wire.answer_fields(
                    session_id=scope_id,
                    question_id=question_id,
                    repo="widgets",
                    answer="First sentence.",
                    final=False,
                )
            ),
            scope_wire.parse_answer(
                scope_wire.answer_fields(
                    session_id=scope_id,
                    question_id=question_id,
                    repo="widgets",
                    answer="Second sentence.",
                    final=True,
                )
            ),
        ],
    )
    assert voice_session.pump_answers() == 2
    assert len(fake_realtime.injected) == 2
    assert question_id not in voice_session._pending


def test_a_failed_search_is_spoken_and_shown_rather_than_swallowed(
    voice_session, fake_realtime, fake_codescope, redis_client, read_stream
) -> None:
    scope_id = seed_scope_session(fake_codescope)
    voice_session.start()
    _call_id, question_id = ask_for(voice_session, fake_realtime)
    queue_answer(
        fake_codescope,
        session_id=scope_id,
        question_id=question_id,
        answer="the agent could not start: no CURSOR_API_KEY",
        status=scope_wire.STATUS_ERROR,
    )
    voice_session.pump_answers()

    assert "CURSOR_API_KEY" in fake_realtime.injected[0]
    assert any("CURSOR_API_KEY" in text for text in events_of(read_stream, wire.KIND_ERROR))
    assert question_id not in voice_session._pending, "a failed question is finished"


def test_asking_with_nothing_loaded_fails_the_tool_instead_of_the_stream(
    voice_session, fake_realtime, fake_codescope, redis_client, read_stream
) -> None:
    voice_session.start()
    call_id = fake_realtime.call_tool(TOOL_ASK_CODEBASE, {"question": QUESTION})
    voice_session.pump_events()

    result = fake_realtime.result_for(call_id)
    assert result["status"] == "error"
    assert "CodeScope" in result["detail"]
    assert fake_codescope.asks == []
    assert voice_session._scope_asks == []


# --- revise my prompt ------------------------------------------------------


ROUGH = "i need a node that take my prompt and make it beter spelling grammer etc"
REWRITE = (
    "Write a node that takes my prompt and improves it: fix spelling, "
    "grammar, and structure while keeping the original intent."
)


def test_revise_my_prompt_returns_immediately_and_publishes_the_ask(
    voice_session, fake_realtime, redis_client, read_stream
) -> None:
    voice_session.start()
    call_id = fake_realtime.call_tool(TOOL_REVISE_MY_PROMPT, {"prompt": ROUGH})
    voice_session.pump_events()

    result = fake_realtime.result_for(call_id)
    assert result["status"] == "revising"
    assert REWRITE_PREFIX in result["detail"]
    assert TOOL_END_SESSION in result["detail"]

    asks = read_stream(sargent_wire.ASK_STREAM)
    assert len(asks) == 1
    _entry_id, ask = asks[0]
    assert set(ask) == set(SARGENT_ASK_CANONICAL_FIELDS)
    assert ask["prompt"] == ROUGH
    assert ask["session_id"] == "voice-test"
    assert ask["prompt_id"] in voice_session._pending
    assert fake_realtime.injected == [], "nothing can be spoken before a rewrite exists"


def test_a_revised_prompt_is_injected_for_the_model_to_speak(
    voice_session, fake_realtime, redis_client, read_stream
) -> None:
    voice_session.start()
    fake_realtime.call_tool(TOOL_REVISE_MY_PROMPT, {"prompt": ROUGH})
    voice_session.pump_events()
    (prompt_id,) = list(voice_session._pending)

    redis_client.xadd(
        sargent_wire.ANSWER_STREAM,
        sargent_wire.answer_fields(
            session_id="voice-test",
            prompt_id=prompt_id,
            rewrite=REWRITE,
        ),
    )
    assert voice_session.pump_answers() == 1

    assert fake_realtime.injected == [f"{REWRITE_PREFIX} {REWRITE}"]
    assert voice_session.state == wire.STATE_SPEAKING

    voice_session.pump_events()
    assert events_of(read_stream, wire.KIND_ANSWER) == [REWRITE], (
        "the marker is an instruction to the model, not something the user hears"
    )
    assert prompt_id not in voice_session._pending


def test_a_failed_rewrite_is_spoken_rather_than_swallowed(
    voice_session, fake_realtime, redis_client, read_stream
) -> None:
    voice_session.start()
    fake_realtime.call_tool(TOOL_REVISE_MY_PROMPT, {"prompt": ROUGH})
    voice_session.pump_events()
    (prompt_id,) = list(voice_session._pending)

    redis_client.xadd(
        sargent_wire.ANSWER_STREAM,
        sargent_wire.answer_fields(
            session_id="voice-test",
            prompt_id=prompt_id,
            rewrite="OPENAI_API_KEY is not set",
            status=sargent_wire.STATUS_ERROR,
        ),
    )
    voice_session.pump_answers()

    assert "OPENAI_API_KEY" in fake_realtime.injected[0]
    assert any("OPENAI_API_KEY" in text for text in events_of(read_stream, wire.KIND_ERROR))
    assert prompt_id not in voice_session._pending


def test_revise_my_prompt_without_text_fails_the_tool_instead_of_the_stream(
    voice_session, fake_realtime, redis_client, read_stream
) -> None:
    voice_session.start()
    call_id = fake_realtime.call_tool(TOOL_REVISE_MY_PROMPT, {"prompt": "   "})
    voice_session.pump_events()

    result = fake_realtime.result_for(call_id)
    assert result["status"] == "error"
    assert read_stream(sargent_wire.ASK_STREAM) == []


# --- dispatch safety -------------------------------------------------------


def test_voice_publishes_a_cloudorder(
    voice_session,
    fake_realtime,
    fake_codescope,
    redis_client,
    read_stream,
    cloudorders,
) -> None:
    """A spoken dispatch is a CLOUDORDER. The URL is read off the session, not said."""
    seed_scope_session(fake_codescope, url="https://github.com/acme/widgets")
    voice_session.start()

    call_id = fake_realtime.call_tool(
        TOOL_DISPATCH_DOC_AGENT,
        {"title": "Document the frame pump", "instructions": "Explain reset in README."},
    )
    voice_session.pump_events()

    result = fake_realtime.result_for(call_id)
    assert result["status"] == cloud_wire.STATUS_QUEUED
    orders = cloudorders()
    assert len(orders) == 1
    assert set(orders[0][1]) == set(CLOUDORDER_CANONICAL_FIELDS)
    assert orders[0][1]["title"] == "Document the frame pump"
    assert orders[0][1]["order_id"] == result["order_id"]
    assert orders[0][1]["auto_pr"] == "true"
    assert read_stream(cloud_wire.CLOUDORDER_STREAM) == []
    assert events_of(read_stream, wire.KIND_DISPATCH) == [
        f"{cloud_wire.STATUS_QUEUED}: Document the frame pump"
    ]


def test_dispatching_without_a_loaded_repo_is_refused(
    voice_session, fake_realtime, redis_client, persistent_client, read_stream
) -> None:
    voice_session.start()
    call_id = fake_realtime.call_tool(
        TOOL_DISPATCH_DOC_AGENT, {"title": "Docs", "instructions": "Write some docs."}
    )
    voice_session.pump_events()

    assert fake_realtime.result_for(call_id)["status"] == "error"
    assert read_stream(cloud_wire.CLOUDORDER_STREAM) == []


# --- work dispatcher tickets -----------------------------------------------


TICKET_REPO = "https://github.com/acme/widgets"


def _list_and_choose(voice_session, fake_realtime, ticket: str = "41") -> None:
    fake_realtime.call_tool(TOOL_LIST_TICKETS, {"repo": TICKET_REPO})
    voice_session.pump_events()
    fake_realtime.call_tool(TOOL_CHOOSE_TICKET, {"ticket": ticket})
    voice_session.pump_events()


def test_list_tickets_reports_how_many_are_waiting(
    voice_session, fake_realtime, fake_gh, redis_client
) -> None:
    fake_gh.add_issue(41, "add-widget-tests", "Cover the widget module.")
    fake_gh.add_issue(42, "docs-pass", "Explain the pump.")
    voice_session.start()

    call_id = fake_realtime.call_tool(TOOL_LIST_TICKETS, {"repo": TICKET_REPO})
    voice_session.pump_events()

    result = fake_realtime.result_for(call_id)
    assert result["status"] == "ok"
    assert result["count"] == 2
    assert {row["id"] for row in result["tickets"]} == {41, 42}


def test_choose_set_and_send_writes_a_workorder(
    voice_session, fake_realtime, fake_gh, redis_client, read_stream
) -> None:
    from megadesk_contracts.wire.machine import parse_workorder

    shot = "https://github.com/user-attachments/assets/voice-shot"
    fake_gh.add_issue(
        41,
        "add-widget-tests",
        f"Cover the widget module with tests. ![shot]({shot})",
    )
    voice_session.start()
    _list_and_choose(voice_session, fake_realtime)

    set_id = fake_realtime.call_tool(
        TOOL_SET_DISPATCH, {"factory": "machine", "model": "grok-4.6"}
    )
    voice_session.pump_events()
    assert fake_realtime.result_for(set_id) == {
        "status": "ok",
        "factory": "machine",
        "model": "grok-4.6",
    }

    send_id = fake_realtime.call_tool(TOOL_SEND_TICKET, {})
    voice_session.pump_events()
    result = fake_realtime.result_for(send_id)
    assert result["status"] == "queued"
    assert result["factory"] == "machine"

    orders = read_stream(machine_wire.WORKORDER_STREAM)
    assert len(orders) == 1
    assert set(orders[0][1]) == set(WORKORDER_CANONICAL_FIELDS)
    assert orders[0][1]["ticket_name"] == "add-widget-tests"
    assert orders[0][1]["model"] == "grok-4.6"
    assert orders[0][1]["URL"] == TICKET_REPO
    assert orders[0][1]["issue"] == "41"
    assert parse_workorder(orders[0][1])["pictures"] == [shot]
    assert read_stream(cloud_wire.CLOUDORDER_STREAM) == []


def test_send_to_cloud_writes_a_cloudorder(
    voice_session, fake_realtime, fake_gh, redis_client, read_stream
) -> None:
    fake_gh.add_issue(41, "add-widget-tests", "Cover the widget module.")
    voice_session.start()
    _list_and_choose(voice_session, fake_realtime)
    fake_realtime.call_tool(TOOL_SET_DISPATCH, {"factory": "cloud"})
    voice_session.pump_events()
    fake_realtime.call_tool(TOOL_SEND_TICKET, {})
    voice_session.pump_events()

    orders = read_stream(cloud_wire.CLOUDORDER_STREAM)
    assert len(orders) == 1
    assert set(orders[0][1]) == set(CLOUDORDER_CANONICAL_FIELDS)
    assert orders[0][1]["title"] == "add-widget-tests"
    assert read_stream(machine_wire.WORKORDER_STREAM) == []


def test_send_without_choosing_is_refused(
    voice_session, fake_realtime, fake_gh, redis_client, read_stream
) -> None:
    voice_session.start()
    call_id = fake_realtime.call_tool(TOOL_SEND_TICKET, {})
    voice_session.pump_events()
    assert fake_realtime.result_for(call_id)["status"] == "error"
    assert read_stream(machine_wire.WORKORDER_STREAM) == []


# --- the rest of the router ------------------------------------------------


def test_set_repo_switches_the_target_and_reports_what_is_loaded(
    voice_session, fake_realtime, fake_codescope, redis_client, read_stream
) -> None:
    seed_scope_session(fake_codescope, repo="widgets")
    seed_scope_session(fake_codescope, repo="gadgets", url="https://github.com/acme/gadgets")
    voice_session.start()

    call_id = fake_realtime.call_tool(TOOL_SET_REPO, {"repo": "gadgets"})
    voice_session.pump_events()

    assert fake_realtime.result_for(call_id) == {"status": "ok", "repo": "gadgets"}
    assert voice_session.target_repo == "gadgets"
    assert events_of(read_stream, wire.KIND_TARGET) == ["gadgets"]


def test_set_repo_for_something_unloaded_names_the_alternatives(
    voice_session, fake_realtime, fake_codescope
) -> None:
    """A bare refusal would leave the model guessing out loud."""
    seed_scope_session(fake_codescope)
    voice_session.start()

    call_id = fake_realtime.call_tool(TOOL_SET_REPO, {"repo": "sprockets"})
    voice_session.pump_events()

    result = fake_realtime.result_for(call_id)
    assert result["status"] == "error"
    assert result["available"] == ["widgets"]


def test_an_unknown_tool_still_gets_a_result(
    voice_session, fake_realtime, redis_client
) -> None:
    """A tool call with no result is a hung conversation, not an error message."""
    voice_session.start()
    call_id = fake_realtime.call_tool("delete_everything", {})
    voice_session.pump_events()

    assert fake_realtime.result_for(call_id)["status"] == "error"


def test_ending_the_session_closes_the_transport(
    voice_session, fake_realtime, redis_client, read_stream
) -> None:
    voice_session.start()
    fake_realtime.call_tool(TOOL_END_SESSION, {})
    voice_session.pump_events()

    assert fake_realtime.closed
    assert voice_session.transport is None
    assert events_of(read_stream, wire.KIND_STATE)[-1] == wire.STATE_OFF


def test_end_session_is_ignored_while_a_search_is_in_flight(
    voice_session, fake_realtime, fake_codescope
) -> None:
    """The logged hang-up: ask_codebase, then end_session a second later."""
    seed_scope_session(fake_codescope)
    voice_session.start()
    fake_realtime.say(QUESTION)
    ask_id = fake_realtime.call_tool(TOOL_ASK_CODEBASE, {"question": QUESTION})
    hangup_id = fake_realtime.call_tool(TOOL_END_SESSION, {})
    voice_session.pump_events()

    assert fake_realtime.result_for(ask_id)["status"] == "searching"
    assert fake_realtime.result_for(hangup_id)["status"] == "error"
    assert not fake_realtime.closed
    assert voice_session.transport is fake_realtime
    assert voice_session._pending, "the search must still be waiting for an answer"


def test_end_session_is_ignored_when_the_user_did_not_say_goodbye(
    voice_session, fake_realtime, fake_codescope, redis_client
) -> None:
    """A completed search is still not a reason to drop the socket."""
    scope_id = seed_scope_session(fake_codescope)
    voice_session.start()
    fake_realtime.say(QUESTION)
    _call_id, question_id = ask_for(voice_session, fake_realtime)
    queue_answer(
        fake_codescope, session_id=scope_id, question_id=question_id, answer=ANSWER
    )
    voice_session.pump_answers()

    hangup_id = fake_realtime.call_tool(TOOL_END_SESSION, {})
    voice_session.pump_events()

    assert fake_realtime.result_for(hangup_id)["status"] == "error"
    assert not fake_realtime.closed
    assert voice_session.transport is fake_realtime


def test_end_session_closes_after_an_explicit_goodbye(
    voice_session, fake_realtime, redis_client, read_stream
) -> None:
    voice_session.start()
    fake_realtime.say("that's all, goodbye")
    fake_realtime.call_tool(TOOL_END_SESSION, {})
    voice_session.pump_events()

    assert fake_realtime.closed
    assert voice_session.transport is None
    assert events_of(read_stream, wire.KIND_STATE)[-1] == wire.STATE_OFF


def test_transcripts_reach_the_frontend_and_audio_does_not(
    voice_session, fake_realtime, redis_client, read_stream
) -> None:
    voice_session.start()
    fake_realtime.say("why does the frame", partial=True)
    fake_realtime.say(QUESTION)
    voice_session.pump_events()

    assert events_of(read_stream, wire.KIND_PARTIAL) == ["why does the frame"]
    assert events_of(read_stream, wire.KIND_FINAL) == [QUESTION]
    for _entry_id, fields in read_stream(wire.EVENT_STREAM):
        assert set(fields) == set(VOICE_EVENT_CANONICAL_FIELDS)
        assert fields["session_id"] == "voice-test"


def test_a_start_command_from_before_the_backend_woke_up_is_ignored(
    voice_session, fake_realtime, redis_client
) -> None:
    """A microphone that switches itself on from stream history is the worst case."""
    redis_client.xadd(
        wire.CONTROL_STREAM, wire.control_fields(action=wire.ACTION_START)
    )

    assert voice_session.pump_controls() == 0
    assert fake_realtime.connected is False


def test_control_messages_start_mute_and_stop_the_transport(
    voice_session, fake_realtime, redis_client
) -> None:
    voice_session.pump_controls()  # take the stream's tail, as the BE does at boot

    redis_client.xadd(
        wire.CONTROL_STREAM, wire.control_fields(action=wire.ACTION_START)
    )
    assert voice_session.pump_controls() == 1
    assert fake_realtime.connected

    for action, expected in (
        (wire.ACTION_MUTE, True),
        (wire.ACTION_UNMUTE, False),
    ):
        redis_client.xadd(wire.CONTROL_STREAM, wire.control_fields(action=action))
        voice_session.pump_controls()
        assert fake_realtime.muted is expected

    redis_client.xadd(wire.CONTROL_STREAM, wire.control_fields(action=wire.ACTION_STOP))
    voice_session.pump_controls()
    assert voice_session.transport is None
    assert fake_realtime.closed


def test_a_transport_that_cannot_open_reports_instead_of_dying(
    redis_client, persistent_client, read_stream
) -> None:
    """A missing API key must leave a BE that still answers its control stream."""
    from VoiceDeckManager.session import VoiceSession

    def explode(**_kwargs):
        raise RuntimeError("OPENAI_API_KEY is not set")

    session = VoiceSession(
        ephemeral=redis_client,
        persistent=persistent_client,
        transport_factory=explode,
        session_id="voice-test",
    )
    try:
        assert session.start() is False
        assert session.state == wire.STATE_ERROR
        assert any(
            "OPENAI_API_KEY" in text for text in events_of(read_stream, wire.KIND_ERROR)
        )
    finally:
        session.shutdown()


def test_state_events_from_the_transport_are_forwarded(
    voice_session, fake_realtime, redis_client, read_stream
) -> None:
    from megadesk_contracts import realtime

    voice_session.start()
    fake_realtime.push(
        RealtimeEvent(kind=realtime.EVENT_STATE, text=wire.STATE_SPEAKING)
    )
    voice_session.pump_events()

    assert events_of(read_stream, wire.KIND_STATE)[-1] == wire.STATE_SPEAKING


# --- the real transport's event mapping ------------------------------------
#
# No socket and no audio device: this only exercises the translation from
# OpenAI's event schema to ours, which is the part a vendor rename breaks.


def transport():
    from VoiceDeckManager.realtime import OpenAIRealtime

    return OpenAIRealtime(api_key="not-used", audio=False)


def test_a_tool_call_is_routable_even_when_it_omits_its_own_name() -> None:
    """The arguments-done event does not reliably carry the function name.

    Only the item that announced the call does, so the name is remembered from
    there. Without that, every tool call would arrive as an unknown tool.
    """
    deck = transport()
    deck._remember_call(
        {"type": "function_call", "call_id": "c1", "name": TOOL_ASK_CODEBASE}
    )

    (event,) = deck._normalize(
        {
            "type": "response.function_call_arguments.done",
            "call_id": "c1",
            "arguments": '{"question": "why does the pump reset"}',
        }
    )
    assert event.name == TOOL_ASK_CODEBASE
    assert event.arguments == {"question": "why does the pump reset"}


def test_speech_starting_drops_queued_audio() -> None:
    """Barge-in: talking over the model must stop it, not produce two voices."""
    deck = transport()
    deck._play("AAAA")
    assert deck._playback.qsize() == 1

    (event,) = deck._normalize({"type": "input_audio_buffer.speech_started"})

    assert event.text == wire.STATE_LISTENING
    assert deck._playback.qsize() == 0


def test_transcripts_and_errors_are_normalized() -> None:
    deck = transport()
    (final,) = deck._normalize(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "transcript": QUESTION,
        }
    )
    (failure,) = deck._normalize({"type": "error", "error": {"message": "nope"}})

    assert final.text == QUESTION
    assert failure.text == "nope"
    assert deck._normalize({"type": "something.invented.last.week"}) == []


def test_the_session_hands_turn_taking_to_the_server() -> None:
    """There is no VAD code in this node, and there should not be."""
    session = transport()._session_update()["session"]
    detection = session["audio"]["input"]["turn_detection"]

    assert detection["type"] == "server_vad"
    assert detection["interrupt_response"] is True
    names = {tool["name"] for tool in session["tools"]}
    assert {
        TOOL_ASK_CODEBASE,
        TOOL_DISPATCH_DOC_AGENT,
        TOOL_SET_REPO,
        TOOL_CREATE_NOTE,
        TOOL_ADD_NOTE_TEXT,
        TOOL_SWITCH_NOTE,
        TOOL_REVISE_MY_PROMPT,
        TOOL_END_SESSION,
        TOOL_LIST_TICKETS,
        TOOL_CHOOSE_TICKET,
        TOOL_SET_DISPATCH,
        TOOL_SEND_TICKET,
        TOOL_LIST_NODES,
        TOOL_SELECT_NODE,
        TOOL_TYPE_INTO,
        TOOL_CLICK_WIDGET,
    } <= names


def test_a_pause_is_not_treated_as_a_hangup() -> None:
    """The model used to hear 'stop talking' / 'done talking' and call end_session."""
    description = next(
        tool["description"]
        for tool in tool_schemas()
        if tool["name"] == TOOL_END_SESSION
    )
    assert "done talking" not in description.lower()
    assert "goodbye" in description.lower()
    assert "Do not call end_session" in INSTRUCTIONS
    assert is_farewell("that's all, goodbye")
    assert is_farewell("hang up")
    assert not is_farewell(QUESTION)


# --- the frontend ----------------------------------------------------------


@pytest.mark.canvas
def test_pressing_listen_publishes_start_and_pressing_it_again_stops(
    harness, redis_client, read_stream
) -> None:
    deck = harness.voice_deck()
    assert deck.get("state_text") == wire.STATE_OFF

    deck.click("talk_btn")
    assert controls(read_stream) == [(wire.ACTION_START, "")]
    harness.wait_until(
        lambda: deck.label("talk_btn") == "stop",
        message="the button to offer a way out",
    )

    deck.click("talk_btn")
    assert controls(read_stream)[-1] == (wire.ACTION_STOP, "")


@pytest.mark.canvas
def test_every_control_the_frontend_sends_is_canonical(
    harness, redis_client, read_stream
) -> None:
    deck = harness.voice_deck()
    deck.click("talk_btn")
    deck.click("mute_btn")

    published = read_stream(wire.CONTROL_STREAM)
    assert len(published) == 2
    for _entry_id, fields in published:
        assert set(fields) == set(VOICE_CONTROL_CANONICAL_FIELDS)
        assert fields["action"] in wire.CONTROL_ACTIONS
    assert published[-1][1] == {"action": wire.ACTION_MUTE, "value": ""}
    assert deck.label("mute_btn") == "live", "the button says what pressing it does"


@pytest.mark.canvas
def test_the_frontend_renders_the_conversation_as_it_happens(
    harness, redis_client, read_stream
) -> None:
    deck = harness.voice_deck()

    for kind, text in (
        (wire.KIND_STATE, wire.STATE_LISTENING),
        (wire.KIND_PARTIAL, "why does the frame"),
        (wire.KIND_FINAL, QUESTION),
        (wire.KIND_ANSWER, ANSWER),
    ):
        redis_client.xadd(
            wire.EVENT_STREAM, wire.event_fields(kind=kind, text=text, session_id="x")
        )

    harness.wait_until(
        lambda: deck.exists("line_2") and deck.get("line_2") == ANSWER,
        message="the answer to be rendered",
    )
    assert deck.get("line_1") == f"you: {QUESTION}"
    assert deck.get("state_text") == wire.STATE_LISTENING
    assert not deck.exists("partial_line"), (
        "a settled turn should replace its own guesswork"
    )


@pytest.mark.canvas
def test_the_repo_combo_offers_what_codescope_has_loaded(
    harness, redis_client, read_stream
) -> None:
    from CodeScopeManager.client import FakeCodeScopeClient, set_client

    fake = FakeCodeScopeClient()
    fake.seed_repo(repo="widgets")
    set_client(fake)
    try:
        deck = harness.voice_deck()
        harness.wait_until(
            lambda: deck.items("repo_target") == ["widgets"],
            message="the combo to find the loaded repo",
        )
        assert deck.get("repo_target") == "widgets"
        deck.select("repo_target", "widgets")
        assert controls(read_stream)[-1] == (wire.ACTION_TARGET, "widgets")
    finally:
        set_client(None)


@pytest.mark.canvas
def test_shutting_down_the_panel_stops_the_conversation(
    harness, redis_client, read_stream
) -> None:
    """A hot microphone with no window attached to it is the failure worth fearing."""
    from voice_deck.panel import shutdown_voice_deck_panel

    deck = harness.voice_deck()
    deck.click("talk_btn")

    shutdown_voice_deck_panel()
    harness.pump(2)

    assert controls(read_stream)[-1] == (wire.ACTION_STOP, "")
