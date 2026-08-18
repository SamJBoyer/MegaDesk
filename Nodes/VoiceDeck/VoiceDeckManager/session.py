"""The voice loop: audio in one side, tool calls and Redis out the other.

The one genuinely hard problem here is timing. A realtime voice model wants a
tool result in well under a second; a Cursor agent takes five to sixty seconds to
answer. Blocking the tool call stalls the conversation and the model goes quiet,
which sounds like a crash.

So the answer is decoupled from the tool result:

1. ``ask_codebase`` publishes CODEQ:ASK and immediately returns ``searching``.
2. The model says something short to hold the floor.
3. CodeScope streams sentences back on CODEQ:ANSWER.
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
from pathlib import Path
from typing import Any, Callable, Optional

from megadesk_contracts import REDIS_DB_PERSISTENT, realtime, resolve_redis_url
from megadesk_contracts.repo import CloneError, remote_url
from megadesk_contracts.wire import cloud as cloud_wire
from megadesk_contracts.wire import code_scope as scope_wire
from megadesk_contracts.wire import voice as wire

from VoiceDeckManager.tools import (
    ANSWER_PREFIX,
    TOOL_ASK_CODEBASE,
    TOOL_DISPATCH_DOC_AGENT,
    TOOL_END_SESSION,
    TOOL_SET_REPO,
    is_farewell,
)

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
        auto_dispatch: bool = False,
    ) -> None:
        self.redis_url = resolve_redis_url(redis_url)
        self.session_id = session_id or scope_wire.new_session_id()
        self.target_repo = repo
        self.auto_dispatch = bool(auto_dispatch)
        self.state = wire.STATE_OFF
        self.transport: Any = None
        self._transport_factory = transport_factory or self._default_transport
        self._ephemeral = ephemeral
        self._persistent = persistent
        self._pending: dict[str, str] = {}
        self._last_user_text = ""
        self._answer_cursor = "$"
        self._control_cursor = "$"
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    # --- Redis ---

    @property
    def ephemeral(self) -> Any:
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

    # --- lifecycle ---

    def _default_transport(self, **kwargs: Any) -> Any:
        from VoiceDeckManager.realtime import OpenAIRealtime

        return OpenAIRealtime(**kwargs)

    def start(self) -> bool:
        """Open the transport and begin listening. Idempotent."""
        if self.transport is not None:
            return True
        self._set_state(wire.STATE_CONNECTING)
        # Arm the answer cursor before any question can exist, so the first read
        # starts from a fixed id rather than "now" and cannot skip an answer.
        self._answer_cursor = self._last_id(scope_wire.ANSWER_STREAM)
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
        elif action == wire.ACTION_AUTO_DISPATCH:
            self.auto_dispatch = wire.is_true(value)
            log.info("auto-dispatch is now %s", self.auto_dispatch)

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
        handlers = {
            TOOL_ASK_CODEBASE: self._tool_ask_codebase,
            TOOL_DISPATCH_DOC_AGENT: self._tool_dispatch_doc_agent,
            TOOL_SET_REPO: self._tool_set_repo,
            TOOL_END_SESSION: self._tool_end_session,
        }
        handler = handlers.get(name)
        if handler is None:
            result = {"status": "error", "detail": f"unknown tool {name}"}
        else:
            try:
                result = handler(arguments, call_id)
            except Exception as exc:  # noqa: BLE001 - a failed tool must answer
                log.exception("Tool %s failed", name)
                result = {"status": "error", "detail": str(exc)}

        if self.transport is not None and call_id:
            self.transport.send_tool_result(call_id, result)

    # --- tools ---

    def _tool_ask_codebase(self, arguments: dict, call_id: str) -> dict:
        question = str(arguments.get("question") or "").strip()
        if not question:
            return {"status": "error", "detail": "no question was provided"}

        resolved = self.resolve_scope_session(self.target_repo)
        if resolved is None:
            detail = "no repository is loaded in CodeScope"
            self._publish(wire.KIND_ERROR, detail)
            return {"status": "error", "detail": detail}

        scope_session_id, repo = resolved
        question_id = scope_wire.new_question_id()
        self.ephemeral.xadd(
            scope_wire.ASK_STREAM,
            scope_wire.ask_fields(
                session_id=scope_session_id,
                question_id=question_id,
                repo=repo,
                question=question,
            ),
        )
        self._pending[question_id] = call_id
        self._set_state(wire.STATE_THINKING)
        # Deliberately not the answer: see the module docstring.
        return {
            "status": "searching",
            "detail": (
                "The answer will arrive shortly as a message beginning with "
                f"{ANSWER_PREFIX}. Say one short thing, then wait silently. "
                f"Do not call {TOOL_END_SESSION}; the session stays open."
            ),
        }

    def _tool_dispatch_doc_agent(self, arguments: dict, call_id: str) -> dict:
        instructions = str(arguments.get("instructions") or "").strip()
        if not instructions:
            return {"status": "error", "detail": "no instructions were provided"}
        title = str(arguments.get("title") or "").strip() or _title_from(instructions)
        repo = str(arguments.get("target") or "").strip() or self.target_repo

        resolved = self.resolve_scope_session(repo)
        if resolved is None:
            detail = "no repository is loaded to dispatch against"
            self._publish(wire.KIND_ERROR, detail)
            return {"status": "error", "detail": detail}
        scope_session_id, repo = resolved

        try:
            url = self._repo_url(scope_session_id)
        except (CloneError, ValueError) as exc:
            detail = f"could not resolve the remote for {repo}: {exc}"
            self._publish(wire.KIND_ERROR, detail)
            return {"status": "error", "detail": detail}

        order_id = cloud_wire.new_order_id()
        order = cloud_wire.cloudorder_fields(
            order_id=order_id,
            repo_url=url,
            title=title,
            instructions=instructions,
            auto_pr=True,
        )
        if self.auto_dispatch:
            self.ephemeral.xadd(cloud_wire.CLOUDORDER_STREAM, order)
            status = cloud_wire.STATUS_QUEUED
        else:
            # A draft, not an order: voice must not open a pull request on its
            # own. It shows up in CloudFactory as a row with a button, and the
            # stored fields are the order verbatim, so dispatching adds nothing.
            self.persistent.hset(cloud_wire.clouddraft_key(order_id), mapping=order)
            status = cloud_wire.STATUS_DRAFT

        self._publish(wire.KIND_DISPATCH, f"{status}: {title}")
        return {
            "status": status,
            "order_id": order_id,
            "title": title,
            "detail": (
                "Queued to run."
                if status == cloud_wire.STATUS_QUEUED
                else "Saved as a draft; tell the user to press dispatch."
            ),
        }

    def _tool_set_repo(self, arguments: dict, call_id: str) -> dict:
        repo = str(arguments.get("repo") or "").strip()
        resolved = self.resolve_scope_session(repo)
        if resolved is None:
            return {
                "status": "error",
                "detail": f"{repo or 'that repository'} is not loaded",
                "available": self.loaded_repos(),
            }
        _session_id, repo = resolved
        self.target_repo = repo
        self._publish(wire.KIND_TARGET, repo)
        return {"status": "ok", "repo": repo}

    def _tool_end_session(self, arguments: dict, call_id: str) -> dict:
        """Close only on an explicit goodbye, never as a follow-up to a search."""
        if self._pending:
            log.info(
                "Ignoring end_session; %d search(es) still in flight",
                len(self._pending),
            )
            return {
                "status": "error",
                "detail": (
                    "A codebase search is still running. Stay on the line and "
                    f"wait for the {ANSWER_PREFIX} message. Do not hang up."
                ),
            }
        if self._last_user_text and not is_farewell(self._last_user_text):
            log.info("Ignoring end_session; last user turn was not a farewell")
            return {
                "status": "error",
                "detail": (
                    "The user did not ask to hang up. Keep listening. "
                    f"Call {TOOL_END_SESSION} only after an explicit goodbye."
                ),
            }
        self.stop()
        return {"status": "ok"}

    # --- answers ---

    def pump_answers(self, *, block_ms: int = 0) -> int:
        """Relay CODEQ:ANSWER entries for questions this session asked."""
        relayed = 0
        for _entry_id, fields in self._read(
            scope_wire.ANSWER_STREAM, self._answer_cursor, block_ms, ANSWER_BATCH
        ):
            try:
                answer = scope_wire.parse_answer(fields)
            except ValueError:
                continue
            if answer["question_id"] not in self._pending:
                continue
            relayed += self._relay(answer)
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
        for key in self.persistent.scan_iter(
            match=f"{scope_wire.SESSION_PREFIX}*", count=100
        ):
            fields = self.persistent.hgetall(key)
            try:
                session = scope_wire.parse_session(fields)
            except ValueError:
                continue
            session_id = scope_wire.session_id_from_key(key)
            if wanted and session["repo"].lower() == wanted:
                return session_id, session["repo"]
            found.append((session_id, session["repo"]))
        if not wanted and len(found) == 1:
            return found[0]
        if wanted:
            return None
        return found[0] if found else None

    def loaded_repos(self) -> list[str]:
        repos: list[str] = []
        for key in self.persistent.scan_iter(
            match=f"{scope_wire.SESSION_PREFIX}*", count=100
        ):
            repo = self.persistent.hget(key, "repo")
            if repo:
                repos.append(repo)
        return sorted(set(repos))

    def _repo_url(self, scope_session_id: str) -> str:
        clone = self.persistent.hget(
            scope_wire.session_key(scope_session_id), "clone_path"
        )
        if not clone:
            raise ValueError("the session has no clone path")
        return remote_url(Path(clone))

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
        elif stream == scope_wire.ANSWER_STREAM:
            self._answer_cursor = cursor

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


def _title_from(instructions: str) -> str:
    words = instructions.split()
    return " ".join(words[:8]) or "documentation change"


def main() -> None:
    VoiceSession().run_forever()


if __name__ == "__main__":
    main()
