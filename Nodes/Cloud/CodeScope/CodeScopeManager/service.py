"""CodeScope without Redis: clone a repo, keep a warm agent, stream answers.

The canvas FE and VoiceDeck are HTTP clients of this process. A JSON session
file under SCOPE_ROOT stands in for a Redis hash, and callers iterate
CODEQ:ANSWER field dicts instead of XREAD.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import Any, Callable, Iterator, Optional
from urllib.parse import urlparse

from megadesk_contracts import (
    AgentError,
    AgentStartupError,
    CloneError,
    ensure_clone,
    refresh_clone,
    repo_name_from_url,
)
from megadesk_contracts.wire import code_scope as wire
from megadesk_contracts.wire.factory import DEFAULT_MODEL

from CodeScopeManager.manager import SentenceBuffer, default_scope_root
from CodeScopeManager.runner import CursorRunner

log = logging.getLogger("code_scope.service")

SESSIONS_FILENAME = "sessions.json"
RunnerFactory = Callable[..., Any]


def normalize_repo_url(git_url: str) -> Optional[tuple[str, str]]:
    """Return ``(url, repo_name)`` for anything git can clone, or ``None``.

    Same rules as the canvas FE: GitHub https and SSH collapse to one clone
    directory; a local path (what the tests clone from) is passed through.
    """
    text = str(git_url or "").strip()
    if not text:
        return None

    ssh = re.match(r"git@github\.com:([^/]+)/([^/]+?)(?:\.git)?/?$", text)
    if ssh:
        owner, repo = ssh.group(1), ssh.group(2)
        return f"https://github.com/{owner}/{repo}", repo

    if text.startswith(("http://", "https://")):
        parsed = urlparse(text)
        if parsed.hostname in ("github.com", "www.github.com"):
            parts = [p for p in parsed.path.strip("/").split("/") if p]
            if len(parts) < 2:
                return None
            owner, repo = parts[0], parts[1]
            if repo.endswith(".git"):
                repo = repo[:-4]
            return f"https://github.com/{owner}/{repo}", repo

    try:
        return text, repo_name_from_url(text)
    except ValueError:
        return None


def public_session(session: dict[str, str]) -> dict[str, str]:
    """The fields an HTTP client needs; clone paths stay on the host."""
    return {
        "session_id": session["session_id"],
        "repo": session["repo"],
        "url": session.get("url") or "",
        "status": session["status"],
        "model": session.get("model") or DEFAULT_MODEL,
    }


class SessionStore:
    """Process memory plus ``SCOPE_ROOT/sessions.json`` so a restart can resume."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.path = self.root / SESSIONS_FILENAME
        self._lock = threading.Lock()
        self._sessions: dict[str, dict[str, str]] = {}
        self.load()

    def load(self) -> None:
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Could not read %s: %s", self.path, exc)
            return
        sessions = raw.get("sessions") if isinstance(raw, dict) else None
        if not isinstance(sessions, dict):
            return
        loaded: dict[str, dict[str, str]] = {}
        for session_id, fields in sessions.items():
            if not isinstance(fields, dict):
                continue
            record = {str(key): str(value) for key, value in fields.items()}
            record["session_id"] = str(session_id)
            loaded[str(session_id)] = record
        self._sessions = loaded

    def _dump(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"sessions": self._sessions}, indent=2, sort_keys=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(self.path)

    def put(self, session: dict[str, str]) -> dict[str, str]:
        session_id = session["session_id"]
        with self._lock:
            stored = dict(session)
            self._sessions[session_id] = stored
            self._dump()
            return dict(stored)

    def get(self, session_id: str) -> dict[str, str]:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise KeyError(session_id)
            return dict(session)

    def find_repo(self, repo: str) -> Optional[dict[str, str]]:
        with self._lock:
            for session in self._sessions.values():
                if session.get("repo") == repo:
                    return dict(session)
        return None

    def all(self) -> list[dict[str, str]]:
        with self._lock:
            return [dict(session) for session in self._sessions.values()]


class ScopeService:
    """Clone intake + one warm Cursor runner per session."""

    def __init__(
        self,
        *,
        root: Optional[Path] = None,
        runner_factory: Optional[RunnerFactory] = None,
        model: str = DEFAULT_MODEL,
    ) -> None:
        self.root = Path(root) if root is not None else default_scope_root()
        self.store = SessionStore(self.root)
        self.runner_factory: RunnerFactory = runner_factory or CursorRunner
        self.model = (model or DEFAULT_MODEL).strip() or DEFAULT_MODEL
        self._runners: dict[str, Any] = {}
        self._ask_lock = threading.Lock()

    def list_sessions(self) -> list[dict[str, str]]:
        return [public_session(session) for session in self.store.all()]

    def get_session(self, session_id: str) -> dict[str, str]:
        return public_session(self.store.get(session_id))

    def open_repo(self, url: str, *, model: str = "") -> dict[str, str]:
        normalized = normalize_repo_url(url)
        if normalized is None:
            raise ValueError("Unrecognized repository URL")
        resolved_url, repo = normalized
        chosen_model = (model or self.model).strip() or self.model

        existing = self.store.find_repo(repo)
        session_id = existing["session_id"] if existing else wire.new_session_id()
        session = {
            "session_id": session_id,
            "url": resolved_url,
            **wire.session_fields(
                repo=repo,
                clone_path=str(self.root / repo),
                model=existing.get("model", chosen_model) if existing else chosen_model,
                status=wire.SESSION_CLONING,
                agent_id=existing.get("agent_id", "") if existing else "",
            ),
        }
        self.store.put(session)
        try:
            clone = ensure_clone(url=resolved_url, root=self.root, name=repo)
        except (CloneError, ValueError) as exc:
            session["status"] = wire.SESSION_ERROR
            self.store.put(session)
            raise CloneError(str(exc)) from exc
        session["clone_path"] = str(clone)
        session["status"] = wire.SESSION_READY
        return self.store.put(session)

    def sync(self, session_id: str) -> dict[str, str]:
        session = self.store.get(session_id)
        sha = refresh_clone(Path(session["clone_path"]))
        session["status"] = wire.SESSION_READY
        self.store.put(session)
        public = public_session(session)
        public["sha"] = sha
        return public

    def ask(
        self,
        session_id: str,
        question: str,
        *,
        mode: str = wire.MODE_ANSWER,
        question_id: str = "",
    ) -> Iterator[dict[str, str]]:
        """Yield CODEQ:ANSWER field dicts. Unknown sessions raise ``KeyError``."""
        text = str(question or "").strip()
        if not text:
            raise ValueError("no question was provided")
        chosen_mode = (mode or wire.MODE_ANSWER).strip() or wire.MODE_ANSWER
        qid = (question_id or "").strip() or wire.new_question_id()

        with self._ask_lock:
            yield from self._ask_locked(
                session_id, text, mode=chosen_mode, question_id=qid
            )

    def close(self) -> None:
        for session_id in list(self._runners):
            self._drop_runner(session_id)

    def _ask_locked(
        self,
        session_id: str,
        question: str,
        *,
        mode: str,
        question_id: str,
    ) -> Iterator[dict[str, str]]:
        session = self.store.get(session_id)
        ask = {
            "session_id": session_id,
            "question_id": question_id,
            "repo": session["repo"],
            "question": question,
            "mode": mode,
        }
        clone = Path(session["clone_path"])
        if not clone.is_dir():
            yield self._error_fields(ask, f"Clone is missing at {clone}")
            session["status"] = wire.SESSION_ERROR
            self.store.put(session)
            return

        session["status"] = wire.SESSION_THINKING
        self.store.put(session)
        try:
            runner = self._runner_for(session_id, session)
            buffer = SentenceBuffer()
            for chunk in runner.answer(question, mode=mode):
                for sentence in buffer.feed(chunk):
                    yield wire.answer_fields(
                        session_id=ask["session_id"],
                        question_id=ask["question_id"],
                        repo=ask["repo"],
                        answer=sentence,
                        final=False,
                    )
            yield wire.answer_fields(
                session_id=ask["session_id"],
                question_id=ask["question_id"],
                repo=ask["repo"],
                answer=buffer.flush(),
                final=True,
            )
            self._remember_agent(session, runner)
            session["status"] = wire.SESSION_READY
            self.store.put(session)
        except AgentStartupError as exc:
            log.error("Agent could not start for %s: %s", clone, exc)
            yield self._error_fields(ask, f"The agent could not start: {exc}")
            self._drop_runner(session_id)
            session["status"] = wire.SESSION_ERROR
            self.store.put(session)
        except AgentError as exc:
            log.error("Agent run failed for %s: %s", clone, exc)
            yield self._error_fields(ask, f"The agent failed: {exc}")
            session["status"] = wire.SESSION_ERROR
            self.store.put(session)

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
            agent_id=session.get("agent_id") or "",
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

    def _remember_agent(self, session: dict[str, str], runner: Any) -> None:
        agent_id = str(getattr(runner, "agent_id", "") or "")
        if agent_id and agent_id != session.get("agent_id"):
            session["agent_id"] = agent_id

    def _error_fields(self, ask: dict[str, str], message: str) -> dict[str, str]:
        return wire.answer_fields(
            session_id=ask["session_id"],
            question_id=ask["question_id"],
            repo=ask["repo"],
            answer=message,
            final=True,
            status=wire.STATUS_ERROR,
        )
