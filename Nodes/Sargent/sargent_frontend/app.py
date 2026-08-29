"""Sargent FE — type a rough prompt, read the rewrite.

Two columns: the left box is the prompt, the right box is the rewrite.
Enter or the send button publishes ``SARGENT:ASK``. The copy button puts both
panels on the clipboard. The FE never calls OpenAI itself; it reads
``SARGENT:ANSWER`` with a plain ``XREAD`` so a later consumer can share the
stream without a group stealing entries.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Optional

import dearpygui.dearpygui as dpg
import redis
from megadesk_contracts import (
    frame_pump,
    redis_connect,
    resolve_ephemeral_db,
    resolve_redis_url,
)
from megadesk_contracts.wire import sargent as wire

log = logging.getLogger("sargent.fe")

POLL_INTERVAL_SEC = 0.4
ANSWER_BATCH = 50

COLOR_GREEN = (80, 200, 80, 255)
COLOR_RED = (220, 70, 70, 255)
COLOR_BLUE = (70, 140, 230, 255)
COLOR_DIM = (90, 90, 90, 255)

_LIVE: dict[str, "Sargent"] = {}


def format_both_panels(prompt: str, output: str) -> str:
    """Clipboard payload: the left panel, a blank line, then the right panel."""
    return f"{prompt}\n\n{output}"


class Sargent:
    def __init__(self) -> None:
        self.session_id = wire.new_session_id()
        self.redis_url = resolve_redis_url()
        self._ui_queue: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._last_answer_id = "$"
        self._pending_prompt_id: Optional[str] = None
        self._root_tag = "primary"
        self._frame_registered = False
        self._redis: Optional[redis.Redis] = None
        self._connect_redis()

    def _tag(self, suffix: str) -> str:
        return f"{self._root_tag}::{suffix}"

    def _widget_text(self, suffix: str) -> str:
        tag = self._tag(suffix)
        if not dpg.does_item_exist(tag):
            return ""
        return dpg.get_value(tag) or ""

    def _connect_redis(self) -> None:
        try:
            self._redis = redis_connect(
                self.redis_url,
                db=resolve_ephemeral_db(self.redis_url),
                socket_connect_timeout=2,
            )
            self._redis.ping()
        except (redis.RedisError, OSError, ValueError):
            self._redis = None

    def build_ui(
        self,
        parent: str,
        *,
        tag_prefix: str,
        width: int = 560,
        height: int = 280,
    ) -> None:
        self._root_tag = tag_prefix
        gutter = 8
        col_w = max(120, (int(width) - gutter) // 2)
        body_h = max(72, int(height) - 48)

        with dpg.group(parent=parent):
            with dpg.group(horizontal=True):
                dpg.add_input_text(
                    tag=self._tag("prompt"),
                    multiline=True,
                    width=col_w,
                    height=body_h,
                    hint="rough prompt",
                    callback=self._on_prompt,
                    on_enter=True,
                    ctrl_enter_for_new_line=True,
                )
                dpg.add_input_text(
                    tag=self._tag("output"),
                    multiline=True,
                    width=col_w,
                    height=body_h,
                    readonly=True,
                )
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="send",
                    width=48,
                    height=22,
                    tag=self._tag("send_btn"),
                    callback=self._on_prompt,
                )
                dpg.add_button(
                    label="copy",
                    width=48,
                    height=22,
                    tag=self._tag("copy_btn"),
                    callback=self._on_copy,
                )
                dpg.add_text("Idle", tag=self._tag("status_text"), color=COLOR_DIM)

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
        self._drain_ui_queue()

    def shutdown(self) -> None:
        self._stop.set()
        if self._frame_registered:
            frame_pump.unregister(self._on_frame)
            self._frame_registered = False
        if self._worker:
            self._worker.join(timeout=2.0)
            self._worker = None
        _LIVE.pop(self._root_tag, None)

    def _on_prompt(self, sender=None, app_data=None, user_data=None) -> None:
        prompt = self._widget_text("prompt").strip()
        if not prompt:
            return
        if self._redis is None:
            self._connect_redis()
        if self._redis is None:
            self._status("Redis unavailable — cannot send", COLOR_RED)
            return

        prompt_id = wire.new_prompt_id()
        payload = wire.ask_fields(
            session_id=self.session_id,
            prompt_id=prompt_id,
            prompt=prompt,
        )
        try:
            self._redis.xadd(wire.ASK_STREAM, payload)
        except redis.RedisError as exc:
            self._status(f"Redis xadd failed: {exc}", COLOR_RED)
            self._redis = None
            return

        self._pending_prompt_id = prompt_id
        self._set_output("…")
        self._status("Rewriting…", COLOR_BLUE)

    def _on_copy(self, sender=None, app_data=None, user_data=None) -> None:
        dpg.set_clipboard_text(
            format_both_panels(self._widget_text("prompt"), self._widget_text("output"))
        )

    def _set_output(self, text: str) -> None:
        tag = self._tag("output")
        if dpg.does_item_exist(tag):
            dpg.set_value(tag, text)

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            self._read_answers()

    def _read_answers(self) -> None:
        if self._redis is None:
            self._connect_redis()
        if self._redis is None:
            self._push(("status", "Redis unavailable", COLOR_RED))
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
            self._stop.wait(POLL_INTERVAL_SEC)
            return

        for _stream, messages in batches or []:
            for entry_id, fields in messages:
                self._last_answer_id = entry_id
                try:
                    answer = wire.parse_answer(fields)
                except ValueError as exc:
                    log.warning("Unusable SARGENT:ANSWER %s: %s", entry_id, exc)
                    continue
                if answer["session_id"] != self.session_id:
                    continue
                self._push(("answer", answer))

    def _push(self, message: tuple) -> None:
        self._ui_queue.put(message)

    def _drain_ui_queue(self) -> None:
        while True:
            try:
                message = self._ui_queue.get_nowait()
            except queue.Empty:
                return
            kind = message[0]
            if kind == "status":
                _, text, color = message
                self._apply_status(text, color)
            elif kind == "answer":
                self._apply_answer(message[1])

    def _status(self, text: str, color: tuple[int, int, int, int]) -> None:
        self._apply_status(text, color)

    def _apply_status(self, text: str, color: tuple[int, int, int, int]) -> None:
        tag = self._tag("status_text")
        if dpg.does_item_exist(tag):
            dpg.set_value(tag, text)
            dpg.configure_item(tag, color=color)

    def _apply_answer(self, answer: dict) -> None:
        if answer["prompt_id"] != self._pending_prompt_id:
            return
        text = (answer["rewrite"] or "").strip() or "(empty)"
        self._set_output(text)
        if answer["status"] == wire.STATUS_ERROR:
            self._apply_status("Rewrite failed", COLOR_RED)
        else:
            self._apply_status("Ready", COLOR_GREEN)


def build_ui(
    parent: str,
    *,
    tag_prefix: str,
    width: int = 560,
    height: int = 280,
) -> None:
    """Module-level builder for FeSpec / MegaDesk canvas hosting."""
    Sargent().build_ui(parent, tag_prefix=tag_prefix, width=width, height=height)


def main() -> None:
    raise SystemExit("Sargent FE is canvas-only. Drop it from the MegaDesk Catalog.")


if __name__ == "__main__":
    main()
