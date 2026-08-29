"""Sargent FE — type a rough prompt, read the rewritten one.

The FE never talks to OpenAI. It publishes ``SARGENT:ASK`` and XREADs
``SARGENT:ANSWER`` on the shared frame pump (no background thread). Answers
are not a consumer group: any other reader of the same rewrite should see it too.
"""

from __future__ import annotations

import logging
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

ANSWER_BATCH = 50

COLOR_GREEN = (80, 200, 80, 255)
COLOR_RED = (220, 70, 70, 255)
COLOR_BLUE = (70, 140, 230, 255)
COLOR_DIM = (90, 90, 90, 255)
COLOR_TEXT_Q = (150, 175, 215, 255)
COLOR_TEXT_A = (205, 205, 205, 255)

_LIVE: dict[str, "Sargent"] = {}


class Sargent:
    def __init__(self) -> None:
        self.redis_url = resolve_redis_url()
        # "0-0" not "$": a rewrite can land between frames, and "$" would miss it.
        self._last_answer_id = "0-0"
        self._prompts: dict[str, int] = {}
        self._root_tag = "primary"
        self._frame_registered = False
        self._row_h = 22
        self._scroll_max: Optional[int] = None
        self._wrap = 380
        self._redis: Optional[redis.Redis] = None
        self._connect_redis()

    def _tag(self, suffix: str) -> str:
        return f"{self._root_tag}::{suffix}"

    def _connect_redis(self) -> None:
        try:
            self._redis = redis_connect(
                self.redis_url,
                db=resolve_ephemeral_db(self.redis_url),
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            self._redis.ping()
        except (redis.RedisError, OSError, ValueError):
            self._redis = None

    def build_ui(
        self,
        parent: str,
        *,
        tag_prefix: str,
        width: int = 420,
        height: int = 220,
    ) -> None:
        self._root_tag = tag_prefix
        self._wrap = max(160, width - 28)
        self._scroll_max = max(self._row_h * 2, height - 52) if height else None

        with dpg.group(parent=parent):
            with dpg.group(horizontal=True):
                dpg.add_text("Idle", tag=self._tag("status_text"), color=COLOR_DIM)
                with dpg.drawlist(width=16, height=16):
                    dpg.draw_circle(
                        (8, 8),
                        6,
                        fill=COLOR_DIM,
                        color=COLOR_DIM,
                        tag=self._tag("conn_light"),
                    )
            dpg.add_child_window(
                tag=self._tag("answer_scroll"),
                width=-1,
                height=self._row_h * 2,
                border=True,
            )
            dpg.add_input_text(
                tag=self._tag("prompt"),
                width=-1,
                hint="rough prompt",
                callback=self._on_prompt,
                on_enter=True,
            )

        dpg.set_item_user_data(parent, self.shutdown)
        if not self._frame_registered:
            frame_pump.register(self._on_frame)
            self._frame_registered = True
        self._set_light(COLOR_GREEN if self._redis is not None else COLOR_RED)
        _LIVE[tag_prefix] = self

    def _on_frame(self) -> None:
        if not dpg.does_item_exist(self._root_tag):
            return
        self._read_answers()

    def shutdown(self) -> None:
        if self._frame_registered:
            frame_pump.unregister(self._on_frame)
            self._frame_registered = False
        if self._redis is not None:
            try:
                self._redis.close()
            except Exception:
                pass
            self._redis = None
        _LIVE.pop(self._root_tag, None)

    def _on_prompt(self, sender=None, app_data=None, user_data=None) -> None:
        tag = self._tag("prompt")
        prompt = (dpg.get_value(tag) or "").strip() if dpg.does_item_exist(tag) else ""
        if not prompt:
            return
        if self._redis is None:
            self._connect_redis()
        if self._redis is None:
            self._status("Redis unavailable", COLOR_RED)
            return

        prompt_id = wire.new_prompt_id()
        try:
            self._redis.xadd(
                wire.ASK_STREAM,
                wire.ask_fields(prompt_id=prompt_id, prompt=prompt),
            )
        except redis.RedisError as exc:
            self._status(f"Redis xadd failed: {exc}", COLOR_RED)
            self._redis = None
            return

        self._add_qa_row(prompt_id, prompt)
        dpg.set_value(tag, "")
        self._status("Rewriting…", COLOR_BLUE)

    def _read_answers(self) -> None:
        if self._redis is None:
            self._connect_redis()
        if self._redis is None:
            self._set_light(COLOR_RED)
            return
        self._set_light(COLOR_GREEN)
        try:
            batches = self._redis.xread(
                {wire.ANSWER_STREAM: self._last_answer_id},
                count=ANSWER_BATCH,
                block=None,
            )
        except redis.RedisError:
            self._redis = None
            self._set_light(COLOR_RED)
            return

        for _stream, messages in batches or []:
            for entry_id, fields in messages:
                self._last_answer_id = entry_id
                try:
                    answer = wire.parse_answer(fields)
                except ValueError as exc:
                    log.warning("Unusable SARGENT:ANSWER %s: %s", entry_id, exc)
                    continue
                if answer["prompt_id"] not in self._prompts:
                    continue
                self._apply_answer(answer)

    def _status(self, text: str, color: tuple[int, int, int, int]) -> None:
        tag = self._tag("status_text")
        if dpg.does_item_exist(tag):
            dpg.set_value(tag, text)
            dpg.configure_item(tag, color=color)

    def _set_light(self, color: tuple[int, int, int, int]) -> None:
        tag = self._tag("conn_light")
        if dpg.does_item_exist(tag):
            dpg.configure_item(tag, fill=color, color=color)

    def _add_qa_row(self, prompt_id: str, prompt: str) -> None:
        index = len(self._prompts) + 1
        self._prompts[prompt_id] = index
        scroll = self._tag("answer_scroll")
        if not dpg.does_item_exist(scroll):
            return
        dpg.add_text(
            prompt,
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
        index = self._prompts.get(answer["prompt_id"])
        if index is None:
            return
        tag = self._tag(f"qa_a_{index}")
        if dpg.does_item_exist(tag):
            failed = answer["status"] == wire.STATUS_ERROR
            dpg.set_value(tag, answer["rewrite"] or "(empty)")
            dpg.configure_item(tag, color=COLOR_RED if failed else COLOR_TEXT_A)
        if answer["status"] == wire.STATUS_ERROR:
            self._status("Rewrite failed", COLOR_RED)
        else:
            self._status("Idle", COLOR_DIM)
        self._resize_scroll()

    def _resize_scroll(self) -> None:
        scroll = self._tag("answer_scroll")
        if not dpg.does_item_exist(scroll):
            return
        rows = max(2, 2 * len(self._prompts))
        height = rows * self._row_h
        if self._scroll_max is not None:
            height = min(height, self._scroll_max)
        dpg.configure_item(scroll, height=height)


def build_ui(
    parent: str,
    *,
    tag_prefix: str,
    width: int = 420,
    height: int = 220,
) -> None:
    """Module-level builder for FeSpec / MegaDesk canvas hosting."""
    Sargent().build_ui(parent, tag_prefix=tag_prefix, width=width, height=height)
