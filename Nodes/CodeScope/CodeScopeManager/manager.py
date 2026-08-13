"""Answer CODEQ:ASK by asking a warm Cursor agent about a clone.

One consumer group on CODEQ:ASK, one agent per session, answers streamed back on
CODEQ:ANSWER as they arrive. Questions are handled one at a time on purpose: a
second question that arrives mid-answer waits, which is what a conversation
expects, and it keeps a single agent per repo from being asked two things at
once.

Every ask is acked, and every ask that cannot be answered gets an error answer
first. Acking without publishing anything would leave the asker — a person
waiting to hear a reply — with nothing but a log line on the BE.
"""

from __future__ import annotations

import logging
import os
import re
import socket
import time
from pathlib import Path
from typing import Any, Callable, Optional

from megadesk_contracts import (
    REDIS_DB_PERSISTENT,
    AgentError,
    AgentStartupError,
    resolve_redis_url,
)
from megadesk_contracts.wire import code_scope as wire

from CodeScopeManager.runner import CursorRunner

log = logging.getLogger("code_scope.manager")

POLL_INTERVAL_SEC = 1.0
ASK_BATCH = 16
RECONNECT_WAIT_SEC = 3.0
# A sentence shorter than this is usually an abbreviation or a numbered list
# marker, not something worth publishing on its own.
MIN_SENTENCE_CHARS = 32

_SENTENCE_END = re.compile(r"[.!?](?=\s)|\n\n")

RunnerFactory = Callable[..., Any]


class SentenceBuffer:
    """Regroup streamed agent text into speakable units.

    The SDK yields whatever the model produced, sometimes a few characters at a
    time. Publishing every fragment would make VoiceDeck speak stutters;
    publishing only at the end would put the first spoken word behind the last
    token. Sentences are the compromise, and they are the unit speech is paced
    in anyway.
    """

    def __init__(self, min_chars: int = MIN_SENTENCE_CHARS) -> None:
        self.min_chars = int(min_chars)
        self._buffer = ""

    def feed(self, text: str) -> list[str]:
        self._buffer += str(text or "")
        out: list[str] = []
        while True:
            match = _SENTENCE_END.search(self._buffer)
            if match is None:
                break
            cut = match.end()
            head, tail = self._buffer[:cut].strip(), self._buffer[cut:]
            if len(head) < self.min_chars:
                # Keep looking: the boundary was inside "e.g." or similar.
                later = _SENTENCE_END.search(self._buffer, cut)
                if later is None:
                    break
                cut = later.end()
                head, tail = self._buffer[:cut].strip(), self._buffer[cut:]
            self._buffer = tail
            if head:
                out.append(head)
        return out

    def flush(self) -> str:
        text = self._buffer.strip()
        self._buffer = ""
        return text


def default_scope_root() -> Path:
    """Where clones live: ``SCOPE_ROOT``, else ``Scope/`` beside this node."""
    configured = (os.environ.get("SCOPE_ROOT") or "").strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parent.parent / "Scope"


class CodeScopeManager:
    """Consume CODEQ:ASK, publish CODEQ:ANSWER."""

    def __init__(
        self,
        *,
        redis_url: Optional[str] = None,
        ephemeral: Any = None,
        persistent: Any = None,
        runner_factory: Optional[RunnerFactory] = None,
        group: str = wire.ASK_GROUP,
        consumer: Optional[str] = None,
        poll_interval: float = POLL_INTERVAL_SEC,
    ) -> None:
        self.redis_url = resolve_redis_url(redis_url)
        self.group = group
        self.consumer = consumer or f"{socket.gethostname()}-{os.getpid()}"
        self.poll_interval = float(poll_interval)
        self.runner_factory: RunnerFactory = runner_factory or CursorRunner
        self._ephemeral = ephemeral
        self._persistent = persistent
        self._runners: dict[str, Any] = {}
        self._group_ready = False

    # --- Redis ---

    @property
    def ephemeral(self) -> Any:
        """Streams live on the database ``REDIS_URL`` names — 0 by default.

        Every other node does the same, so pointing ``REDIS_URL`` at a different
        database moves the whole pipeline together. Only the session hash is
        pinned to db 1, because it has to outlive the stream traffic.
        """
        if self._ephemeral is None:
            import redis

            self._ephemeral = redis.Redis.from_url(
                self.redis_url, decode_responses=True
            )
        return self._ephemeral

    @property
    def persistent(self) -> Any:
        if self._persistent is None:
            import redis

            self._persistent = redis.Redis.from_url(
                self.redis_url, db=REDIS_DB_PERSISTENT, decode_responses=True
            )
        return self._persistent

    def ensure_group(self) -> None:
        if self._group_ready:
            return
        from redis.exceptions import ResponseError

        try:
            self.ephemeral.xgroup_create(
                wire.ASK_STREAM, self.group, id="0", mkstream=True
            )
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        self._group_ready = True

    # --- loop ---

    def poll_once(self) -> int:
        """Drain pending then new asks. Returns how many were handled."""
        self.ensure_group()
        handled = 0
        for stream_id in ("0", ">"):
            results = self.ephemeral.xreadgroup(
                groupname=self.group,
                consumername=self.consumer,
                streams={wire.ASK_STREAM: stream_id},
                count=ASK_BATCH,
            )
            for _stream, messages in results or []:
                for entry_id, fields in messages:
                    self._process(entry_id, fields)
                    handled += 1
        return handled

    def run_forever(self) -> None:
        log.info(
            "CodeScope manager up: %s group=%s consumer=%s",
            wire.ASK_STREAM,
            self.group,
            self.consumer,
        )
        while True:
            try:
                self.poll_once()
                time.sleep(self.poll_interval)
            except KeyboardInterrupt:
                log.info("Interrupted; shutting down")
                return
            except Exception:  # noqa: BLE001 - a long-lived BE outlives Redis restarts
                log.exception("Poll failed; retrying in %.1fs", RECONNECT_WAIT_SEC)
                self._group_ready = False
                time.sleep(RECONNECT_WAIT_SEC)

    def close(self) -> None:
        for session_id, runner in list(self._runners.items()):
            try:
                runner.close()
            except Exception:  # noqa: BLE001
                log.warning("Runner close failed for session %s", session_id)
        self._runners.clear()

    # --- work ---

    def _process(self, entry_id: str, fields: dict[str, Any]) -> None:
        try:
            self.handle_ask(entry_id, fields)
        except Exception:  # noqa: BLE001
            log.exception("Unhandled error handling CODEQ:ASK %s", entry_id)
        finally:
            self.ephemeral.xack(wire.ASK_STREAM, self.group, entry_id)

    def handle_ask(self, entry_id: str, fields: dict[str, Any]) -> None:
        try:
            ask = wire.parse_ask(fields)
        except ValueError as exc:
            log.error("Unusable CODEQ:ASK %s: %s", entry_id, exc)
            return

        session_key = wire.session_key(ask["session_id"])
        raw = self.persistent.hgetall(session_key)
        if not raw:
            self._publish_error(
                ask,
                f"No CodeScope session {ask['session_id']}. Re-enter the repository URL.",
            )
            return

        try:
            session = wire.parse_session(raw)
        except ValueError as exc:
            self._publish_error(ask, f"Session {ask['session_id']} is unusable: {exc}")
            return

        clone = Path(session["clone_path"])
        if not clone.is_dir():
            self._publish_error(ask, f"Clone is missing at {clone}")
            return

        log.info(
            "Question session=%s repo=%s mode=%s",
            ask["session_id"],
            ask["repo"],
            ask["mode"],
        )
        self._set_status(session_key, wire.SESSION_THINKING)
        try:
            runner = self._runner_for(ask["session_id"], session)
            buffer = SentenceBuffer()
            for chunk in runner.answer(ask["question"], mode=ask["mode"]):
                for sentence in buffer.feed(chunk):
                    self._publish(ask, sentence, final=False)
            # The tail is published final even when empty: a reader needs to know
            # the answer is over, and an agent that said nothing is still an end.
            self._publish(ask, buffer.flush(), final=True)
            self._remember_agent(session_key, session, runner)
            self._set_status(session_key, wire.SESSION_READY)
        except AgentStartupError as exc:
            log.error("Agent could not start for %s: %s", clone, exc)
            self._publish_error(ask, f"The agent could not start: {exc}")
            # Drop the runner: a half-open agent will fail the same way forever,
            # and the next question deserves a fresh attempt.
            self._drop_runner(ask["session_id"])
        except AgentError as exc:
            log.error("Agent run failed for %s: %s", clone, exc)
            self._publish_error(ask, f"The agent failed: {exc}")

    # --- runners ---

    def _runner_for(self, session_id: str, session: dict[str, str]) -> Any:
        clone = str(Path(session["clone_path"]))
        cached = self._runners.get(session_id)
        if cached is not None and str(getattr(cached, "cwd", "")) == clone:
            return cached
        if cached is not None:
            self._drop_runner(session_id)
        runner = self.runner_factory(
            cwd=Path(clone),
            model=session["model"],
            agent_id=session["agent_id"],
        )
        self._runners[session_id] = runner
        return runner

    def _drop_runner(self, session_id: str) -> None:
        runner = self._runners.pop(session_id, None)
        if runner is None:
            return
        try:
            runner.close()
        except Exception:  # noqa: BLE001
            log.warning("Runner close failed for session %s", session_id)

    def _remember_agent(
        self, session_key: str, session: dict[str, str], runner: Any
    ) -> None:
        """Persist the agent id so a restarted BE resumes instead of starting cold."""
        agent_id = str(getattr(runner, "agent_id", "") or "")
        if agent_id and agent_id != session.get("agent_id"):
            self.persistent.hset(session_key, "agent_id", agent_id)
            session["agent_id"] = agent_id

    # --- publishing ---

    def _publish(self, ask: dict[str, str], text: str, *, final: bool) -> None:
        payload = wire.answer_fields(
            session_id=ask["session_id"],
            question_id=ask["question_id"],
            repo=ask["repo"],
            answer=text,
            final=final,
        )
        self.ephemeral.xadd(wire.ANSWER_STREAM, payload)

    def _publish_error(self, ask: dict[str, str], message: str) -> None:
        payload = wire.answer_fields(
            session_id=ask["session_id"],
            question_id=ask["question_id"],
            repo=ask["repo"],
            answer=message,
            final=True,
            status=wire.STATUS_ERROR,
        )
        self.ephemeral.xadd(wire.ANSWER_STREAM, payload)
        try:
            self._set_status(wire.session_key(ask["session_id"]), wire.SESSION_ERROR)
        except Exception:  # noqa: BLE001 - the answer already went out
            log.debug("Could not mark session %s errored", ask["session_id"])

    def _set_status(self, session_key: str, status: str) -> None:
        if self.persistent.exists(session_key):
            self.persistent.hset(session_key, "status", status)


def main() -> None:
    manager = CodeScopeManager()
    try:
        manager.run_forever()
    finally:
        manager.close()


if __name__ == "__main__":
    main()
