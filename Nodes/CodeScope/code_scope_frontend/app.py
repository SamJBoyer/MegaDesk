"""CodeScope FE — point it at a repo, ask about the code, read the answer.

The FE owns repo intake: it clones in a background thread and records the result
on ``CODESCOPE:SESSION:<id>`` (persistent DB), which is how the BE learns where to bind its
agent. It never touches ``cursor_sdk`` itself, so a slow or failed agent shows up
here as an error answer rather than a frozen canvas.

Answers are read with a plain ``XREAD``, deliberately not a consumer group:
VoiceDeck reads the same CODEQ:ANSWER entries, and a group would let whichever
process asked first steal them.
"""

from __future__ import annotations

import logging
import queue
import re
import threading
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import dearpygui.dearpygui as dpg
import redis
from megadesk_contracts import (
    ensure_clone,
    frame_pump,
    refresh_clone,
    redis_connect,
    repo_name_from_url,
    resolve_ephemeral_db,
    resolve_persistent_db,
    resolve_redis_url,
)
from megadesk_contracts.repo import CloneError
from megadesk_contracts.wire import code_scope as wire

log = logging.getLogger("code_scope.fe")

POLL_INTERVAL_SEC = 0.4
ANSWER_BATCH = 50
DEFAULT_MODEL = "auto"
MODEL_OPTIONS = ("auto", "composer-2.5")

COLOR_GREEN = (80, 200, 80, 255)
COLOR_RED = (220, 70, 70, 255)
COLOR_BLUE = (70, 140, 230, 255)
COLOR_AMBER = (215, 170, 70, 255)
COLOR_DIM = (90, 90, 90, 255)
COLOR_TEXT_Q = (150, 175, 215, 255)
COLOR_TEXT_A = (205, 205, 205, 255)

# Keep live instances alive while their host content windows exist.
_LIVE: dict[str, "CodeScope"] = {}


def scope_root() -> Path:
    """Where clones land. Mirrors ``CodeScopeManager.default_scope_root``."""
    from CodeScopeManager.manager import default_scope_root

    return default_scope_root()


def normalize_repo_url(git_url: str) -> Optional[tuple[str, str]]:
    """Return ``(url, repo_name)`` for anything git can clone, or ``None``.

    GitHub https and SSH forms are normalized so the same repo pasted two ways
    produces one clone. Anything else git understands — including a local path,
    which is what the integration suite clones from — is passed through.
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


class CodeScope:
    def __init__(self) -> None:
        self.session_id = wire.new_session_id()
        self.redis_url = resolve_redis_url()
        self._ui_queue: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._refresh_request = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._url_lock = threading.Lock()
        self._current_url = ""
        self._prepared_url = ""
        self._clone: Optional[Path] = None
        self._repo = ""
        self._ready = False
        self._last_answer_id = "$"
        self._questions: dict[str, int] = {}
        self._answers: dict[str, str] = {}
        self._root_tag = "primary"
        self._frame_registered = False
        self._row_h = 22
        self._scroll_max: Optional[int] = None
        self._wrap = 460
        self._redis: Optional[redis.Redis] = None
        self._persistent: Optional[redis.Redis] = None
        self._connect_redis()

    # --- plumbing ---

    def _tag(self, suffix: str) -> str:
        return f"{self._root_tag}::{suffix}"

    def _connect_redis(self) -> None:
        try:
            self._redis = redis_connect(
                self.redis_url,
                db=resolve_ephemeral_db(self.redis_url),
                socket_connect_timeout=2,
            )
            self._redis.ping()
            self._persistent = redis_connect(
                self.redis_url,
                db=resolve_persistent_db(self.redis_url),
                socket_connect_timeout=2,
            )
        except (redis.RedisError, OSError, ValueError):
            self._redis = None
            self._persistent = None

    # --- UI ---

    def build_ui(
        self,
        parent: str,
        *,
        tag_prefix: str,
        width: int = 520,
        height: int = 240,
    ) -> None:
        self._root_tag = tag_prefix
        self._wrap = max(160, width - 28)
        # Chrome (url + status + question row) is ~74px; the log takes the rest
        # and starts at two rows so an empty node stays small.
        self._scroll_max = max(self._row_h * 2, height - 74) if height else None

        with dpg.group(parent=parent):
            with dpg.group(horizontal=True):
                dpg.add_input_text(
                    tag=self._tag("git_url"),
                    width=-116,
                    hint="https://github.com/owner/repo",
                    callback=self._on_url_changed,
                    on_enter=True,
                )
                dpg.add_combo(
                    items=list(MODEL_OPTIONS),
                    default_value=DEFAULT_MODEL,
                    width=74,
                    height_mode=dpg.mvComboHeight_Small,
                    tag=self._tag("model"),
                )
                with dpg.drawlist(width=16, height=16):
                    dpg.draw_circle(
                        (8, 8),
                        6,
                        fill=COLOR_DIM,
                        color=COLOR_DIM,
                        tag=self._tag("conn_light"),
                    )
            dpg.add_text("Idle", tag=self._tag("status_text"), color=COLOR_DIM)
            with dpg.group(horizontal=True):
                dpg.add_input_text(
                    tag=self._tag("question"),
                    width=-64,
                    hint="ask about this code",
                    callback=self._on_question,
                    on_enter=True,
                )
                dpg.add_button(
                    label="sync",
                    width=56,
                    height=22,
                    tag=self._tag("refresh_btn"),
                    callback=self._on_refresh,
                )
            dpg.add_child_window(
                tag=self._tag("answer_scroll"),
                width=-1,
                height=self._row_h * 2,
                border=True,
            )

        dpg.set_item_user_data(parent, self.shutdown)
        self._start_services()
        _LIVE[tag_prefix] = self

    def _start_services(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._stop.clear()
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()
        if not self._frame_registered:
            frame_pump.register(self._on_frame)
            self._frame_registered = True

    def _on_frame(self) -> None:
        if not dpg.does_item_exist(self._root_tag):
            return
        self._sync_url_from_input()
        self._drain_ui_queue()

    def shutdown(self) -> None:
        self._stop.set()
        if self._frame_registered:
            frame_pump.unregister(self._on_frame)
            self._frame_registered = False
        if self._worker:
            self._worker.join(timeout=2.0)
            self._worker = None
        # The session is minted per FE instance, so leaving the hash behind would
        # just accumulate dead keys (and stale agent ids) on the persistent DB.
        if self._persistent is not None:
            try:
                self._persistent.delete(wire.session_key(self.session_id))
            except redis.RedisError:
                pass
        _LIVE.pop(self._root_tag, None)

    # --- callbacks ---

    def _sync_url_from_input(self) -> None:
        tag = self._tag("git_url")
        if not dpg.does_item_exist(tag):
            return
        with self._url_lock:
            self._current_url = (dpg.get_value(tag) or "").strip()

    def _on_url_changed(self, sender=None, app_data=None, user_data=None) -> None:
        self._sync_url_from_input()
        self._status("Resolving repository…", COLOR_AMBER)

    def _on_refresh(self, sender=None, app_data=None, user_data=None) -> None:
        if self._clone is None:
            self._status("Nothing cloned yet", COLOR_DIM)
            return
        # The worker does the git work: a fetch against a real remote takes long
        # enough to stall the render thread visibly.
        self._refresh_request.set()
        self._status("Syncing clone…", COLOR_AMBER)

    def _on_question(self, sender=None, app_data=None, user_data=None) -> None:
        tag = self._tag("question")
        question = (dpg.get_value(tag) or "").strip() if dpg.does_item_exist(tag) else ""
        if not question:
            return
        if not self._ready:
            self._status("Repository is not ready yet", COLOR_AMBER)
            return
        if self._redis is None:
            self._connect_redis()
        if self._redis is None:
            self._status("Redis unavailable — cannot ask", COLOR_RED)
            return

        question_id = wire.new_question_id()
        payload = wire.ask_fields(
            session_id=self.session_id,
            question_id=question_id,
            repo=self._repo,
            question=question,
            mode=wire.MODE_ANSWER,
        )
        try:
            self._redis.xadd(wire.ASK_STREAM, payload)
        except redis.RedisError as exc:
            self._status(f"Redis xadd failed: {exc}", COLOR_RED)
            self._redis = None
            return

        self._add_qa_row(question_id, question)
        dpg.set_value(tag, "")
        self._status("Thinking…", COLOR_BLUE)

    # --- worker ---

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            if self._refresh_request.is_set():
                self._refresh_request.clear()
                self._do_refresh()

            with self._url_lock:
                url = self._current_url

            if not url:
                self._push(("conn", False))
                self._push(("status", "Enter a repository URL", COLOR_DIM))
                self._stop.wait(POLL_INTERVAL_SEC)
                continue

            if url != self._prepared_url:
                self._prepare(url)
                continue

            if self._ready:
                self._read_answers()
            else:
                self._stop.wait(POLL_INTERVAL_SEC)

    def _prepare(self, url: str) -> None:
        """Clone if needed and publish the session the BE reads."""
        resolved = normalize_repo_url(url)
        if resolved is None:
            self._ready = False
            self._prepared_url = url
            self._push(("conn", False))
            self._push(("status", "Unrecognized repository URL", COLOR_RED))
            return

        clone_url, repo = resolved
        self._push(("status", f"Cloning {repo}…", COLOR_AMBER))
        try:
            clone = ensure_clone(url=clone_url, root=scope_root(), name=repo)
        except (CloneError, ValueError) as exc:
            self._ready = False
            self._prepared_url = url
            self._push(("conn", False))
            self._push(("status", f"Clone failed: {exc}", COLOR_RED))
            return

        self._clone = clone
        self._repo = repo
        self._prepared_url = url
        if not self._write_session(repo, clone):
            return

        self._ready = True
        self._last_answer_id = "$"
        self._push(("conn", True))
        self._push(("status", f"Ready — {repo}", COLOR_GREEN))

    def _write_session(self, repo: str, clone: Path) -> bool:
        if self._persistent is None:
            self._connect_redis()
        if self._persistent is None:
            self._ready = False
            self._push(("conn", False))
            self._push(("status", "Redis unavailable — session not published", COLOR_RED))
            return False

        model = DEFAULT_MODEL
        tag = self._tag("model")
        if dpg.does_item_exist(tag):
            model = (dpg.get_value(tag) or "").strip() or DEFAULT_MODEL

        try:
            self._persistent.hset(
                wire.session_key(self.session_id),
                mapping=wire.session_fields(
                    repo=repo,
                    clone_path=str(clone),
                    model=model,
                    status=wire.SESSION_READY,
                ),
            )
        except redis.RedisError as exc:
            self._ready = False
            self._push(("status", f"Redis session write failed: {exc}", COLOR_RED))
            return False
        return True

    def _read_answers(self) -> None:
        if self._redis is None:
            self._connect_redis()
        if self._redis is None:
            self._push(("conn", False))
            self._stop.wait(POLL_INTERVAL_SEC)
            return
        try:
            block_ms = max(50, int(POLL_INTERVAL_SEC * 1000))
            batches = self._redis.xread(
                {wire.ANSWER_STREAM: self._last_answer_id},
                count=ANSWER_BATCH,
                block=block_ms,
            )
        except redis.RedisError:
            self._redis = None
            self._push(("conn", False))
            self._stop.wait(POLL_INTERVAL_SEC)
            return

        for _stream, messages in batches or []:
            for entry_id, fields in messages:
                self._last_answer_id = entry_id
                try:
                    answer = wire.parse_answer(fields)
                except ValueError as exc:
                    log.warning("Unusable CODEQ:ANSWER %s: %s", entry_id, exc)
                    continue
                if answer["session_id"] != self.session_id:
                    continue
                self._push(("answer", answer))

    def _push(self, message: tuple) -> None:
        self._ui_queue.put(message)

    # --- UI drain ---

    def _drain_ui_queue(self) -> None:
        while True:
            try:
                message = self._ui_queue.get_nowait()
            except queue.Empty:
                return

            kind = message[0]
            if kind == "conn":
                self._set_light(COLOR_GREEN if message[1] else COLOR_RED)
            elif kind == "status":
                _, text, color = message
                self._apply_status(text, color)
            elif kind == "answer":
                self._apply_answer(message[1])

    def _do_refresh(self) -> None:
        """Hard-reset the clone. Worker thread only."""
        if self._clone is None:
            return
        try:
            sha = refresh_clone(self._clone)
        except (CloneError, ValueError) as exc:
            self._push(("status", f"Sync failed: {exc}", COLOR_RED))
            return
        self._push(("status", f"Synced {self._repo} at {sha[:8]}", COLOR_GREEN))

    def _status(self, text: str, color: tuple[int, int, int, int]) -> None:
        """Set status from the UI thread; the worker uses the queue instead."""
        self._apply_status(text, color)

    def _apply_status(self, text: str, color: tuple[int, int, int, int]) -> None:
        tag = self._tag("status_text")
        if dpg.does_item_exist(tag):
            dpg.set_value(tag, text)
            dpg.configure_item(tag, color=color)

    def _set_light(self, color: tuple[int, int, int, int]) -> None:
        tag = self._tag("conn_light")
        if dpg.does_item_exist(tag):
            dpg.configure_item(tag, fill=color, color=color)

    def _add_qa_row(self, question_id: str, question: str) -> None:
        index = len(self._questions) + 1
        self._questions[question_id] = index
        self._answers[question_id] = ""
        scroll = self._tag("answer_scroll")
        if not dpg.does_item_exist(scroll):
            return
        dpg.add_text(
            f"? {question}",
            parent=scroll,
            wrap=self._wrap,
            color=COLOR_TEXT_Q,
            tag=self._tag(f"qa_q_{index}"),
        )
        dpg.add_text(
            "…",
            parent=scroll,
            wrap=self._wrap,
            color=COLOR_TEXT_A,
            tag=self._tag(f"qa_a_{index}"),
        )
        self._resize_scroll()

    def _apply_answer(self, answer: dict) -> None:
        question_id = answer["question_id"]
        index = self._questions.get(question_id)
        if index is None:
            # An answer to a question this FE did not ask — VoiceDeck's, most
            # likely, sharing the session. Show it rather than dropping it.
            index = len(self._questions) + 1
            self._questions[question_id] = index
            self._answers[question_id] = ""
            scroll = self._tag("answer_scroll")
            if dpg.does_item_exist(scroll):
                dpg.add_text(
                    "? (asked by voice)",
                    parent=scroll,
                    wrap=self._wrap,
                    color=COLOR_TEXT_Q,
                    tag=self._tag(f"qa_q_{index}"),
                )
                dpg.add_text(
                    "…",
                    parent=scroll,
                    wrap=self._wrap,
                    color=COLOR_TEXT_A,
                    tag=self._tag(f"qa_a_{index}"),
                )

        chunk = answer["answer"].strip()
        if chunk:
            existing = self._answers.get(question_id, "")
            self._answers[question_id] = f"{existing} {chunk}".strip()

        tag = self._tag(f"qa_a_{index}")
        if dpg.does_item_exist(tag):
            text = self._answers.get(question_id) or "(no answer)"
            dpg.set_value(tag, text)
            failed = answer["status"] == wire.STATUS_ERROR
            dpg.configure_item(tag, color=COLOR_RED if failed else COLOR_TEXT_A)

        if answer["final"]:
            if answer["status"] == wire.STATUS_ERROR:
                self._apply_status("Answer failed", COLOR_RED)
            else:
                self._apply_status(f"Ready — {self._repo}", COLOR_GREEN)
        self._resize_scroll()

    def _resize_scroll(self) -> None:
        scroll = self._tag("answer_scroll")
        if not dpg.does_item_exist(scroll):
            return
        rows = max(2, 2 * len(self._questions))
        height = rows * self._row_h
        if self._scroll_max is not None:
            height = min(height, self._scroll_max)
        dpg.configure_item(scroll, height=height)


def build_ui(
    parent: str,
    *,
    tag_prefix: str,
    width: int = 520,
    height: int = 240,
) -> None:
    """Module-level builder for FeSpec / MegaDesk canvas hosting."""
    CodeScope().build_ui(parent, tag_prefix=tag_prefix, width=width, height=height)


def main() -> None:
    raise SystemExit("CodeScope FE is canvas-only. Drop it from the MegaDesk Catalog.")


if __name__ == "__main__":
    main()
