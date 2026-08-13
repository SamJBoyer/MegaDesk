"""CodeScope end to end: paste a repo, ask a question, read the answer.

The chain is cut where the money is spent. ``FakeCodeAgent`` stands in for
``cursor_sdk`` and the model; everything on both sides of that cut is real — the
canvas and its widget callbacks, the git clone on disk, the CODEQ:ASK consumer
group, the session hash on db 1, and the streamed CODEQ:ANSWER payloads.

Two seams get their own tests because they fail independently:

* FE ↔ stream, with ``FakeCodeAgent`` standing in for the whole BE.
* BE ↔ agent, with the real ``CodeScopeManager`` loop and a fake runner, which is
  where sentence buffering, acks, ``agent_id`` persistence and error answers live.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import (
    CODEQ_ANSWER_CANONICAL_FIELDS,
    CODEQ_ASK_CANONICAL_FIELDS,
    CODEQ_ASK_GROUP,
)
from megadesk_contracts.wire import code_scope as wire

pytestmark = [pytest.mark.canvas, pytest.mark.redis, pytest.mark.git]

QUESTION = "Why does the frame pump need a reset?"
ANSWER = (
    "The pump is a module global that outlives the Dear PyGui context. "
    "Whoever owns the context resets it on teardown."
)


# --- helpers ---------------------------------------------------------------


def host_code_scope(harness, origin: Path):
    """Drop CodeScope, point it at a repo, and wait until it is ready to ask."""
    scope = harness.drop("code_scope")
    scope.type_into("git_url", str(origin))
    harness.wait_until(
        lambda: scope.get("status_text").startswith("Ready"),
        message="CodeScope to finish cloning",
    )
    return scope


def session_of(persistent_client) -> tuple[str, dict[str, str]]:
    """The one CODESCOPE:SESSION hash on db 1, as (session_id, fields)."""
    keys = list(persistent_client.scan_iter(match=f"{wire.SESSION_PREFIX}*"))
    assert len(keys) == 1, f"expected exactly one session hash, found {keys}"
    return wire.session_id_from_key(keys[0]), persistent_client.hgetall(keys[0])


def ask_through_manager(
    redis_client, persistent_client, *, session_id: str, repo: str, question: str
) -> str:
    """XADD a CODEQ:ASK the way any asker would. Returns the question id."""
    question_id = wire.new_question_id()
    redis_client.xadd(
        wire.ASK_STREAM,
        wire.ask_fields(
            session_id=session_id,
            question_id=question_id,
            repo=repo,
            question=question,
        ),
    )
    return question_id


def answers_for(read_stream, question_id: str) -> list[dict[str, str]]:
    return [
        fields
        for _entry_id, fields in read_stream(wire.ANSWER_STREAM)
        if fields.get("question_id") == question_id
    ]


def seed_session(persistent_client, *, repo: str, clone_path: Path) -> str:
    session_id = wire.new_session_id()
    persistent_client.hset(
        wire.session_key(session_id),
        mapping=wire.session_fields(repo=repo, clone_path=str(clone_path)),
    )
    return session_id


# --- FE intake -------------------------------------------------------------


def test_pasting_a_repo_clones_it_and_publishes_the_session(
    harness, redis_client, persistent_client, scope_root: Path, origin_repo: Path
) -> None:
    """The FE owns intake: the BE only ever learns where the clone landed."""
    scope = host_code_scope(harness, origin_repo)

    clone = scope_root / "widgets"
    assert (clone / ".git").exists(), f"no clone at {clone}"
    assert (clone / "README.md").is_file(), "clone has no working tree"

    _session_id, fields = session_of(persistent_client)
    assert fields["repo"] == "widgets"
    assert Path(fields["clone_path"]) == clone
    assert fields["status"] == wire.SESSION_READY
    assert scope.get("status_text") == "Ready — widgets"


def test_an_unusable_url_is_reported_and_nothing_is_cloned(
    harness, redis_client, persistent_client, scope_root: Path
) -> None:
    scope = harness.drop("code_scope")
    scope.type_into("git_url", "not a repo at all")
    harness.wait_until(
        lambda: "Unrecognized" in scope.get("status_text"),
        message="CodeScope to reject the URL",
    )
    assert not scope_root.exists()
    assert list(persistent_client.scan_iter(match=f"{wire.SESSION_PREFIX}*")) == []


def test_the_session_hash_is_removed_when_the_node_is_closed(
    harness, redis_client, persistent_client, scope_root: Path, origin_repo: Path
) -> None:
    """A session is minted per FE instance, so a stale hash is just a dead key."""
    scope = host_code_scope(harness, origin_repo)
    session_id, _fields = session_of(persistent_client)

    scope.close()
    harness.pump(2)

    assert persistent_client.exists(wire.session_key(session_id)) == 0


# --- FE asking -------------------------------------------------------------


def test_asking_publishes_a_canonical_ask_and_shows_the_streamed_answer(
    harness,
    redis_client,
    persistent_client,
    read_stream,
    fake_code_agent,
    scope_root: Path,
    origin_repo: Path,
) -> None:
    scope = host_code_scope(harness, origin_repo)
    session_id, _fields = session_of(persistent_client)
    fake_code_agent.add_answer("frame pump", ANSWER)

    scope.type_into("question", QUESTION)

    asks = read_stream(wire.ASK_STREAM)
    assert len(asks) == 1
    _entry_id, ask = asks[0]
    assert set(ask) == set(CODEQ_ASK_CANONICAL_FIELDS)
    assert ask["question"] == QUESTION
    assert ask["repo"] == "widgets"
    assert ask["session_id"] == session_id
    assert ask["mode"] == wire.MODE_ANSWER
    assert scope.get("question") == "", "the input should clear once the ask is sent"

    # The stand-in BE answers in sentences, as the streaming runner does.
    runs = fake_code_agent.run_once()
    assert len(runs) == 1
    assert len(runs[0].chunks) == 2, "a two-sentence answer should stream in two parts"

    harness.wait_until(
        lambda: "outlives" in scope.get("qa_a_1"),
        message="the answer to reach the log",
    )
    assert scope.get("qa_q_1") == f"? {QUESTION}"
    assert scope.get("qa_a_1") == ANSWER
    harness.wait_until(
        lambda: scope.get("status_text").startswith("Ready"),
        message="status to settle after the final answer",
    )


def test_every_answer_entry_is_canonical_and_only_the_last_is_final(
    harness,
    redis_client,
    persistent_client,
    read_stream,
    fake_code_agent,
    scope_root: Path,
    origin_repo: Path,
) -> None:
    scope = host_code_scope(harness, origin_repo)
    fake_code_agent.add_answer("frame pump", ANSWER)
    scope.type_into("question", QUESTION)
    fake_code_agent.run_once()

    entries = [fields for _entry_id, fields in read_stream(wire.ANSWER_STREAM)]
    assert len(entries) == 2
    for fields in entries:
        assert set(fields) == set(CODEQ_ANSWER_CANONICAL_FIELDS)
        assert fields["status"] == wire.STATUS_OK
    assert [f["final"] for f in entries] == ["false", "true"]


def test_a_question_asked_before_the_clone_is_ready_is_refused(
    harness, redis_client, read_stream, scope_root: Path
) -> None:
    """Publishing it would only produce an error answer from the BE."""
    scope = harness.drop("code_scope")
    scope.type_into("question", QUESTION)

    assert read_stream(wire.ASK_STREAM) == []
    assert "not ready" in scope.get("status_text")


def test_a_failed_answer_is_surfaced_rather_than_swallowed(
    harness,
    redis_client,
    persistent_client,
    fake_code_agent,
    scope_root: Path,
    origin_repo: Path,
) -> None:
    scope = host_code_scope(harness, origin_repo)
    fake_code_agent.error = "The agent could not start: no CURSOR_API_KEY"

    scope.type_into("question", QUESTION)
    fake_code_agent.run_once()

    harness.wait_until(
        lambda: scope.get("status_text") == "Answer failed",
        message="the FE to report the failed answer",
    )
    assert "CURSOR_API_KEY" in scope.get("qa_a_1")


def test_sync_hard_resets_the_clone(
    harness, redis_client, persistent_client, scope_root: Path, origin_repo: Path
) -> None:
    """The clone is disposable, which is what makes the agent's write tools safe."""
    host_code_scope(harness, origin_repo)
    clone = scope_root / "widgets"
    stray = clone / "stray.txt"
    stray.write_text("an agent that ignored its instructions\n", encoding="utf-8")
    (clone / "README.md").write_text("locally mangled\n", encoding="utf-8")

    scope = harness.driver_for("code_scope")
    scope.click("refresh_btn")
    harness.wait_until(
        lambda: scope.get("status_text").startswith("Synced"),
        message="the clone to be synced",
    )

    assert not stray.exists()
    assert (clone / "README.md").read_text(encoding="utf-8") == "seed repo\n"


# --- BE loop ---------------------------------------------------------------


def test_the_manager_streams_sentences_and_marks_only_the_last_final(
    redis_client,
    persistent_client,
    read_stream,
    code_scope_manager,
    fake_code_agent,
    tmp_path: Path,
) -> None:
    fake_code_agent.add_answer("frame pump", ANSWER)
    session_id = seed_session(persistent_client, repo="widgets", clone_path=tmp_path)
    question_id = ask_through_manager(
        redis_client,
        persistent_client,
        session_id=session_id,
        repo="widgets",
        question=QUESTION,
    )

    assert code_scope_manager.poll_once() == 1

    published = answers_for(read_stream, question_id)
    # One entry per sentence: the first as the agent produces it, the last only
    # once the run ends, which is what marks it final.
    assert [f["final"] for f in published] == ["false", "true"]
    assert published[0]["answer"].startswith("The pump is a module global")
    assert published[1]["answer"].startswith("Whoever owns the context")
    assert all(f["status"] == wire.STATUS_OK for f in published)
    assert fake_code_agent.questions == [QUESTION]
    assert fake_code_agent.modes == [wire.MODE_ANSWER]


def test_the_manager_persists_the_agent_id_so_a_restart_can_resume(
    redis_client, persistent_client, code_scope_manager, fake_code_agent, tmp_path: Path
) -> None:
    session_id = seed_session(persistent_client, repo="widgets", clone_path=tmp_path)
    ask_through_manager(
        redis_client,
        persistent_client,
        session_id=session_id,
        repo="widgets",
        question=QUESTION,
    )
    code_scope_manager.poll_once()

    fields = persistent_client.hgetall(wire.session_key(session_id))
    assert fields["agent_id"] == fake_code_agent.agent_id
    assert fields["status"] == wire.SESSION_READY


def test_the_manager_reuses_one_agent_across_questions(
    redis_client, persistent_client, code_scope_manager, fake_code_agent, tmp_path: Path
) -> None:
    """A cold agent per question would cost seconds a conversation cannot spare."""
    session_id = seed_session(persistent_client, repo="widgets", clone_path=tmp_path)
    for question in (QUESTION, "And where is it registered?"):
        ask_through_manager(
            redis_client,
            persistent_client,
            session_id=session_id,
            repo="widgets",
            question=question,
        )
        code_scope_manager.poll_once()

    assert len(fake_code_agent.runners) == 1
    assert len(fake_code_agent.questions) == 2


def test_an_ask_for_an_unknown_session_still_gets_an_answer(
    redis_client, persistent_client, read_stream, code_scope_manager
) -> None:
    """Acking in silence would leave whoever asked waiting forever."""
    question_id = ask_through_manager(
        redis_client,
        persistent_client,
        session_id="never-existed",
        repo="widgets",
        question=QUESTION,
    )
    code_scope_manager.poll_once()

    published = answers_for(read_stream, question_id)
    assert len(published) == 1
    assert published[0]["status"] == wire.STATUS_ERROR
    assert published[0]["final"] == "true"
    assert "never-existed" in published[0]["answer"]


def test_an_agent_that_cannot_start_is_reported_and_the_ask_is_acked(
    redis_client,
    persistent_client,
    read_stream,
    code_scope_manager,
    fake_code_agent,
    tmp_path: Path,
) -> None:
    fake_code_agent.startup_error = "no CURSOR_API_KEY in the environment"
    session_id = seed_session(persistent_client, repo="widgets", clone_path=tmp_path)
    question_id = ask_through_manager(
        redis_client,
        persistent_client,
        session_id=session_id,
        repo="widgets",
        question=QUESTION,
    )
    code_scope_manager.poll_once()

    published = answers_for(read_stream, question_id)
    assert len(published) == 1
    assert published[0]["status"] == wire.STATUS_ERROR
    assert "CURSOR_API_KEY" in published[0]["answer"]
    assert persistent_client.hget(wire.session_key(session_id), "status") == (
        wire.SESSION_ERROR
    )

    pending = redis_client.xpending(wire.ASK_STREAM, CODEQ_ASK_GROUP)
    count = pending.get("pending") if isinstance(pending, dict) else pending[0]
    assert int(count or 0) == 0, "a handled ask must not stay pending"


def test_a_missing_clone_is_reported_rather_than_asked_about(
    redis_client, persistent_client, read_stream, code_scope_manager, tmp_path: Path
) -> None:
    session_id = seed_session(
        persistent_client, repo="widgets", clone_path=tmp_path / "gone"
    )
    question_id = ask_through_manager(
        redis_client,
        persistent_client,
        session_id=session_id,
        repo="widgets",
        question=QUESTION,
    )
    code_scope_manager.poll_once()

    published = answers_for(read_stream, question_id)
    assert published[0]["status"] == wire.STATUS_ERROR
    assert "missing" in published[0]["answer"].lower()
