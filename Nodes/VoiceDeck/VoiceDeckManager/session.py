"""The voice loop: audio in one side, tool calls and Redis out the other.

The one genuinely hard problem here is timing. A realtime voice model wants a
tool result in well under a second; a Cursor agent takes five to sixty seconds to
answer. Blocking the tool call stalls the conversation and the model goes quiet,
which sounds like a crash.

So the answer is decoupled from the tool result:

1. ``ask_codebase`` queues an HTTP ask against CodeScope and immediately returns
   ``searching``.
2. The model says something short to hold the floor.
3. CodeScope streams sentences back over SSE.
4. Each sentence is injected as a new conversation item plus a ``response.create``,
   so the model speaks it as it arrives.

That is why answers are pumped on their own thread rather than inside the event
loop: the transport's event iterator blocks on a socket, and an answer that
arrived during that block would wait for the user to speak again.

No audio crosses Redis. Only transcripts, state, and control messages do.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Callable, Optional

from megadesk_contracts import resolve_ephemeral_db, resolve_persistent_db, redis_connect, realtime, resolve_redis_url
from megadesk_contracts.wire import code_scope as scope_wire
from megadesk_contracts.wire import sargent as sargent_wire
from megadesk_contracts.wire import voice as wire

from VoiceDeckManager.tools import ANSWER_PREFIX, REWRITE_PREFIX, tool_handlers

log = logging.getLogger("voice_deck.session")

CONTROL_BATCH = 32
ANSWER_BATCH = 50
IDLE_BLOCK_MS = 1000
ANSWER_BLOCK_MS = 250

TransportFactory = Callable[..., Any]


class VoiceSession:
    """One VoiceDeck backend: control plane, tool router, and answer relay."""

    def __init__(
        self,
        *,
        redis_url: Optional[str] = None,
        ephemeral: Any = None,
        persistent: Any = None,
        transport_factory: Optional[TransportFactory] = None,
        session_id: str = "",
        repo: str = "",
        codescope: Any = None,
    ) -> None:
        self.redis_url = resolve_redis_url(redis_url)
        self.session_id = session_id or scope_wire.new_session_id()
        self.target_repo = repo
        self.state = wire.STATE_OFF
        self.transport: Any = None
        self._transport_factory = transport_factory or self._default_transport
        self._ephemeral = ephemeral
        self._persistent = persistent
        self._codescope = codescope
        self._pending: dict[str, str] = {}
        self._scope_asks: list[tuple[str, str, str, str]] = []
        self._last_user_text = ""
        self.current_call_id = ""
        self._rewrite_cursor = "$"
        self._control_cursor = "$"
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    # --- Redis ---

    @property
    def ephemeral(self) -> Any:
        if self._ephemeral is None:
            self._ephemeral = redis_connect(
                self.redis_url, db=resolve_ephemeral_db(self.redis_url)
            )
        return self._ephemeral

    @property
    def persistent(self) -> Any:
        if self._persistent is None:
            self._persistent = redis_connect(
                self.redis_url, db=resolve_persistent_db(self.redis_url)
            )
        return self._persistent

    # --- lifecycle ---

    def _default_transport(self, **kwargs: Any) -> Any:
        from VoiceDeckManager.realtime import OpenAIRealtime

        return OpenAIRealtime(**kwargs)

    def start(self) -> bool:
        """Open the transport and begin listening. Idempotent."""
        if self.transport is not None:
            return True
        self._set_state(wire.STATE_CONNECTING)
        self._rewrite_cursor = self._last_id(sargent_wire.ANSWER_STREAM)
        try:
            self.transport = self._transport_factory()
            self.transport.connect()
        except Exception as exc:  # noqa: BLE001 - a bad key must not kill the BE
            log.exception("Could not open the voice transport")
            self.transport = None
            self._publish(wire.KIND_ERROR, f"Could not start voice: {exc}")
            self._set_state(wire.STATE_ERROR)
            return False
        self._set_state(wire.STATE_LISTENING)
        return True

    def stop(self) -> None:
        transport, self.transport = self.transport, None
        self._pending.clear()
        self._last_user_text = ""
        if transport is not None:
            try:
                transport.close()
            except Exception:  # noqa: BLE001
                log.warning("Transport close failed", exc_info=True)
        self._set_state(wire.STATE_OFF)

    def shutdown(self) -> None:
        self._stop.set()
        self.stop()
        for thread in self._threads:
            thread.join(timeout=2.0)
        self._threads.clear()

    # --- control plane ---

    def pump_controls(self, *, block_ms: int = 0) -> int:
        """Apply queued VOICE:CONTROL messages. Returns how many were applied."""
        applied = 0
        for _entry_id, fields in self._read(
            wire.CONTROL_STREAM, self._control_cursor, block_ms, CONTROL_BATCH
        ):
            try:
                control = wire.parse_control(fields)
            except ValueError as exc:
                log.warning("Unusable VOICE:CONTROL: %s", exc)
                continue
            self.apply_control(control["action"], control["value"])
            applied += 1
        return applied

    def apply_control(self, action: str, value: str = "") -> None:
        if action == wire.ACTION_START:
            self.start()
        elif action == wire.ACTION_STOP:
            self.stop()
        elif action in (wire.ACTION_MUTE, wire.ACTION_UNMUTE):
            muted = action == wire.ACTION_MUTE
            if self.transport is not None:
                self.transport.set_muted(muted)
            self._set_state(wire.STATE_MUTED if muted else wire.STATE_LISTENING)
        elif action == wire.ACTION_TARGET:
            self.target_repo = value.strip()
            self._publish(wire.KIND_TARGET, self.target_repo or "(none)")

    # --- realtime events ---

    def pump_events(self, *, limit: int = 0) -> int:
        """Drain transport events. Blocks with a real socket; returns with a fake."""
        if self.transport is None:
            return 0
        handled = 0
        for event in self.transport.events():
            self.handle_event(event)
            handled += 1
            if limit and handled >= limit:
                break
            if self._stop.is_set():
                break
        return handled

    def handle_event(self, event: Any) -> None:
        kind = getattr(event, "kind", "")
        text = getattr(event, "text", "") or ""

        if kind == realtime.EVENT_TRANSCRIPT_PARTIAL:
            if text.strip():
                self._publish(wire.KIND_PARTIAL, text)
        elif kind == realtime.EVENT_TRANSCRIPT_FINAL:
            if text.strip():
                self._last_user_text = text.strip()
                self._publish(wire.KIND_FINAL, text)
                self._set_state(wire.STATE_THINKING)
        elif kind == realtime.EVENT_ASSISTANT_TEXT:
            # Published here rather than at injection time: this is what the user
            # actually heard, and injected text is only a prompt to say it.
            if text.strip():
                self._publish(wire.KIND_ANSWER, self._strip_marker(text))
            self._set_state(wire.STATE_LISTENING)
        elif kind == realtime.EVENT_TOOL_CALL:
            self._route_tool(event)
        elif kind == realtime.EVENT_STATE:
            if text in wire.VOICE_STATES:
                self._set_state(text)
        elif kind == realtime.EVENT_ERROR:
            self._publish(wire.KIND_ERROR, text or "unknown realtime error")
        else:
            log.debug("Ignoring transport event kind=%s", kind)

    def _route_tool(self, event: Any) -> None:
        name = getattr(event, "name", "") or ""
        call_id = getattr(event, "call_id", "") or ""
        arguments = getattr(event, "arguments", None) or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments or "{}")
            except json.JSONDecodeError:
                arguments = {}

        log.info("Tool call %s args=%s", name, sorted(arguments))
        self.current_call_id = call_id
        handler = tool_handlers().get(name)
        if handler is None:
            result = {"status": "error", "detail": f"unknown tool {name}"}
        else:
            try:
                result = handler(arguments, self)
            except Exception as exc:  # noqa: BLE001 - a failed tool must answer
                log.exception("Tool %s failed", name)
                result = {"status": "error", "detail": str(exc)}

        if self.transport is not None and call_id:
            self.transport.send_tool_result(call_id, result)

    # --- ToolHost ---

    @property
    def pending(self) -> dict[str, str]:
        return self._pending

    @property
    def last_user_text(self) -> str:
        return self._last_user_text

    def publish(self, kind: str, text: str) -> None:
        self._publish(kind, text)

    def set_state(self, state: str) -> None:
        self._set_state(state)

    def remember_question(self, question_id: str, call_id: str) -> None:
        self._pending[question_id] = call_id

    def queue_scope_ask(
        self,
        session_id: str,
        question: str,
        question_id: str,
        *,
        mode: str = "",
    ) -> None:
        self._scope_asks.append(
            (session_id, question, question_id, mode or scope_wire.MODE_ANSWER)
        )

    def repo_url(self, scope_session_id: str) -> str:
        return self._repo_url(scope_session_id)

    @property
    def codescope(self) -> Any:
        if self._codescope is None:
            from CodeScopeManager.client import get_client

            self._codescope = get_client()
        return self._codescope

    # --- answers ---

    def pump_answers(self, *, block_ms: int = 0) -> int:
        """Relay CodeScope SSE and SARGENT:ANSWER for asks this session made."""
        relayed = self._pump_scope_asks()
        for _entry_id, fields in self._read(
            sargent_wire.ANSWER_STREAM, self._rewrite_cursor, block_ms, ANSWER_BATCH
        ):
            try:
                rewrite = sargent_wire.parse_answer(fields)
            except ValueError:
                continue
            if rewrite["prompt_id"] not in self._pending:
                continue
            relayed += self._relay_rewrite(rewrite)
        return relayed

    def _pump_scope_asks(self) -> int:
        relayed = 0
        queued = self._scope_asks
        self._scope_asks = []
        for session_id, question, question_id, mode in queued:
            try:
                for answer in self.codescope.ask(
                    session_id,
                    question,
                    mode=mode,
                    question_id=question_id,
                ):
                    relayed += self._relay(answer)
            except Exception as exc:  # noqa: BLE001 - a failed search must speak
                log.exception("CodeScope ask failed")
                relayed += self._relay(
                    {
                        "session_id": session_id,
                        "question_id": question_id,
                        "repo": "",
                        "answer": str(exc),
                        "final": True,
                        "status": scope_wire.STATUS_ERROR,
                    }
                )
        return relayed

    def _relay(self, answer: dict) -> int:
        question_id = answer["question_id"]
        text = answer["answer"].strip()
        failed = answer["status"] == scope_wire.STATUS_ERROR

        if failed:
            self._pending.pop(question_id, None)
            self._publish(wire.KIND_ERROR, text)
            self._speak(f"I could not search the code: {text}")
            return 1

        if answer["final"]:
            self._pending.pop(question_id, None)
        if not text:
            return 0
        self._speak(f"{ANSWER_PREFIX} {text}")
        return 1

    def _relay_rewrite(self, answer: dict) -> int:
        prompt_id = answer["prompt_id"]
        text = answer["rewrite"].strip()
        failed = answer["status"] == sargent_wire.STATUS_ERROR
        self._pending.pop(prompt_id, None)
        if failed:
            self._publish(wire.KIND_ERROR, text)
            self._speak(f"I could not revise the prompt: {text}")
            return 1
        if not text:
            return 0
        self._speak(f"{REWRITE_PREFIX} {text}")
        return 1

    def _speak(self, text: str) -> None:
        if self.transport is None:
            log.debug("No transport; dropping %r", text[:60])
            return
        self.transport.inject_assistant_text(text)
        self._set_state(wire.STATE_SPEAKING)

    @staticmethod
    def _strip_marker(text: str) -> str:
        cleaned = text.strip()
        if cleaned.startswith(ANSWER_PREFIX):
            cleaned = cleaned[len(ANSWER_PREFIX) :].strip()
        elif cleaned.startswith(REWRITE_PREFIX):
            cleaned = cleaned[len(REWRITE_PREFIX) :].strip()
        return cleaned

    # --- CodeScope sessions ---

    def resolve_scope_session(self, repo: str = "") -> Optional[tuple[str, str]]:
        """Find the CodeScope session to ask, as ``(session_id, repo)``.

        Prefers an exact repo match, and falls back to the only loaded repo when
        there is exactly one — which is the common case, and saves the user from
        having to name it out loud.
        """
        wanted = (repo or "").strip().lower()
        found: list[tuple[str, str]] = []
        try:
            sessions = self.codescope.list_repos()
        except Exception:  # noqa: BLE001
            log.warning("Could not list CodeScope repos", exc_info=True)
            return None
        for session in sessions:
            name = str(session.get("repo") or "")
            session_id = str(session.get("session_id") or "")
            if not name or not session_id:
                continue
            if wanted and name.lower() == wanted:
                return session_id, name
            found.append((session_id, name))
        if not wanted and len(found) == 1:
            return found[0]
        if wanted:
            return None
        return found[0] if found else None

    def loaded_repos(self) -> list[str]:
        try:
            sessions = self.codescope.list_repos()
        except Exception:  # noqa: BLE001
            log.warning("Could not list CodeScope repos", exc_info=True)
            return []
        return sorted({str(item.get("repo") or "") for item in sessions if item.get("repo")})

    def _repo_url(self, scope_session_id: str) -> str:
        session = self.codescope.get_session(scope_session_id)
        url = str(session.get("url") or "").strip()
        if not url:
            raise ValueError("the session has no repository URL")
        return url

    # --- plumbing ---

    def _last_id(self, stream: str) -> str:
        """The newest id on a stream, or ``0-0`` when it is empty."""
        try:
            newest = self.ephemeral.xrevrange(stream, count=1)
        except Exception:  # noqa: BLE001 - an unreachable Redis is not fatal here
            log.warning("Could not read the tail of %s", stream, exc_info=True)
            return "0-0"
        return newest[0][0] if newest else "0-0"

    def _read(
        self, stream: str, cursor: str, block_ms: int, count: int
    ) -> list[tuple[str, dict[str, str]]]:
        if cursor == "$":
            # Resolve "now" to a real id once. Left as "$", every poll would mean
            # "newer than this instant", losing anything that arrived in the gap
            # between two polls.
            cursor = self._last_id(stream)
            self._remember(stream, cursor)
        kwargs: dict[str, Any] = {"count": count}
        if block_ms:
            kwargs["block"] = int(block_ms)
        try:
            batches = self.ephemeral.xread({stream: cursor}, **kwargs)
        except Exception:  # noqa: BLE001 - Redis restarts must not kill the BE
            log.warning("Read of %s failed", stream, exc_info=True)
            return []

        entries: list[tuple[str, dict[str, str]]] = []
        for _stream, messages in batches or []:
            for entry_id, fields in messages:
                entries.append((entry_id, fields))
        if entries:
            self._remember(stream, entries[-1][0])
        return entries

    def _remember(self, stream: str, cursor: str) -> None:
        if stream == wire.CONTROL_STREAM:
            self._control_cursor = cursor
        elif stream == sargent_wire.ANSWER_STREAM:
            self._rewrite_cursor = cursor

    def _publish(self, kind: str, text: str) -> None:
        try:
            self.ephemeral.xadd(
                wire.EVENT_STREAM,
                wire.event_fields(kind=kind, text=text, session_id=self.session_id),
            )
        except Exception:  # noqa: BLE001 - the FE losing a line is not fatal
            log.warning("Could not publish VOICE:EVENT kind=%s", kind, exc_info=True)

    def _set_state(self, state: str) -> None:
        if state == self.state:
            return
        self.state = state
        self._publish(wire.KIND_STATE, state)

    # --- production loop ---

    def run_forever(self) -> None:
        """Idle on VOICE:CONTROL until told to start, then run all three pumps.

        The transport is not opened at boot: a microphone that goes live because a
        node was launched is not something anyone asked for.
        """
        log.info("VoiceDeck up, idle. Waiting for %s start", wire.CONTROL_STREAM)
        self._set_state(wire.STATE_OFF)
        self._threads = [
            threading.Thread(target=self._answer_loop, daemon=True),
            threading.Thread(target=self._event_loop, daemon=True),
        ]
        for thread in self._threads:
            thread.start()
        try:
            from megadesk_contracts import node_should_stop

            while not self._stop.is_set() and not node_should_stop():
                self.pump_controls(block_ms=IDLE_BLOCK_MS)
        except KeyboardInterrupt:
            log.info("Interrupted; shutting down")
        finally:
            self.shutdown()

    def _answer_loop(self) -> None:
        while not self._stop.is_set():
            if self.transport is None:
                time.sleep(0.2)
                continue
            self.pump_answers(block_ms=ANSWER_BLOCK_MS)

    def _event_loop(self) -> None:
        while not self._stop.is_set():
            if self.transport is None:
                time.sleep(0.2)
                continue
            try:
                self.pump_events()
            except Exception:  # noqa: BLE001 - a dropped socket is recoverable
                log.warning("Voice transport failed; closing", exc_info=True)
                self.stop()


def main() -> None:
    VoiceSession().run_forever()


if __name__ == "__main__":
    main()
