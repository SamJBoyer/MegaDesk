"""VoiceDeck FE — press to talk, watch what was heard and said.

Hosted as canvas chrome (`voice_deck.panel`), not a Catalog node.

Nothing here touches audio. The BE owns the microphone, the speaker and the
realtime socket; this half sends control messages and renders text, which is why
a stalled canvas can never stutter the conversation and a stalled conversation can
never freeze the canvas.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Optional

import dearpygui.dearpygui as dpg
import redis
from megadesk_contracts import resolve_ephemeral_db, resolve_persistent_db, redis_connect, frame_pump, resolve_redis_url
from megadesk_contracts.wire import code_scope as scope_wire
from megadesk_contracts.wire import voice as wire

log = logging.getLogger("voice_deck.fe")

POLL_INTERVAL_SEC = 0.3
EVENT_BATCH = 50
MAX_LINES = 40

COLOR_GREEN = (80, 200, 80, 255)
COLOR_RED = (220, 70, 70, 255)
COLOR_BLUE = (70, 140, 230, 255)
COLOR_AMBER = (215, 170, 70, 255)
COLOR_DIM = (90, 90, 90, 255)
COLOR_YOU = (150, 175, 215, 255)
COLOR_SAID = (205, 205, 205, 255)

STATE_COLORS = {
    wire.STATE_OFF: COLOR_DIM,
    wire.STATE_CONNECTING: COLOR_AMBER,
    wire.STATE_LISTENING: COLOR_GREEN,
    wire.STATE_THINKING: COLOR_BLUE,
    wire.STATE_SPEAKING: COLOR_BLUE,
    wire.STATE_MUTED: COLOR_AMBER,
    wire.STATE_ERROR: COLOR_RED,
}

_LIVE: dict[str, "VoiceDeck"] = {}


class VoiceDeck:
    def __init__(self) -> None:
        self.redis_url = resolve_redis_url()
        self._ui_queue: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._cursor = "$"
        self._state = wire.STATE_OFF
        self._muted = False
        self._lines: list[str] = []
        self._line_seq = 0
        self._root_tag = "primary"
        self._frame_registered = False
        self._row_h = 20
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
        width: int = 460,
        height: int = 200,
    ) -> None:
        self._root_tag = tag_prefix
        self._wrap = max(160, width - 28)
        self._scroll_max = max(self._row_h * 2, height - 52) if height else None

        with dpg.group(parent=parent):
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="listen",
                    width=54,
                    height=22,
                    tag=self._tag("talk_btn"),
                    callback=self._on_talk,
                )
                dpg.add_button(
                    label="mute",
                    width=46,
                    height=22,
                    tag=self._tag("mute_btn"),
                    callback=self._on_mute,
                )
                dpg.add_combo(
                    items=[],
                    width=-20,
                    height_mode=dpg.mvComboHeight_Small,
                    tag=self._tag("repo_target"),
                    callback=self._on_target,
                )
                with dpg.drawlist(width=16, height=16):
                    dpg.draw_circle(
                        (8, 8),
                        6,
                        fill=COLOR_DIM,
                        color=COLOR_DIM,
                        tag=self._tag("state_light"),
                    )
            dpg.add_text("off", tag=self._tag("state_text"), color=COLOR_DIM)
            dpg.add_child_window(
                tag=self._tag("transcript"),
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
        self._drain_ui_queue()

    def shutdown(self) -> None:
        # Stop the conversation with the panel: a hot microphone with no window
        # attached to it is the one failure mode worth being careful about.
        self._send(wire.ACTION_STOP)
        self._stop.set()
        if self._frame_registered:
            frame_pump.unregister(self._on_frame)
            self._frame_registered = False
        if self._worker:
            self._worker.join(timeout=2.0)
            self._worker = None
        _LIVE.pop(self._root_tag, None)

    # --- callbacks ---

    def _on_talk(self, sender=None, app_data=None, user_data=None) -> None:
        starting = self._state == wire.STATE_OFF
        self._send(wire.ACTION_START if starting else wire.ACTION_STOP)
        self._apply_state(wire.STATE_CONNECTING if starting else wire.STATE_OFF)

    def _on_mute(self, sender=None, app_data=None, user_data=None) -> None:
        self._muted = not self._muted
        self._send(wire.ACTION_MUTE if self._muted else wire.ACTION_UNMUTE)
        self._label("mute_btn", "live" if self._muted else "mute")

    def _on_target(self, sender=None, app_data=None, user_data=None) -> None:
        tag = self._tag("repo_target")
        repo = (dpg.get_value(tag) or "").strip() if dpg.does_item_exist(tag) else ""
        if repo:
            self._send(wire.ACTION_TARGET, repo)

    def _send(self, action: str, value: str = "") -> None:
        if self._redis is None:
            self._connect_redis()
        if self._redis is None:
            self._apply_status("Redis unavailable", COLOR_RED)
            return
        try:
            self._redis.xadd(
                wire.CONTROL_STREAM, wire.control_fields(action=action, value=value)
            )
        except redis.RedisError as exc:
            self._apply_status(f"Redis xadd failed: {exc}", COLOR_RED)
            self._redis = None

    # --- worker ---

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            self._refresh_repos()
            self._read_events()

    def _refresh_repos(self) -> None:
        if self._persistent is None:
            return
        try:
            repos = sorted(
                {
                    repo
                    for key in self._persistent.scan_iter(
                        match=f"{scope_wire.SESSION_PREFIX}*", count=100
                    )
                    if (repo := self._persistent.hget(key, "repo"))
                }
            )
        except redis.RedisError:
            return
        self._ui_queue.put(("repos", repos))

    def _read_events(self) -> None:
        if self._redis is None:
            self._connect_redis()
        if self._redis is None:
            self._stop.wait(POLL_INTERVAL_SEC)
            return
        try:
            batches = self._redis.xread(
                {wire.EVENT_STREAM: self._cursor},
                count=EVENT_BATCH,
                block=max(50, int(POLL_INTERVAL_SEC * 1000)),
            )
        except redis.RedisError:
            self._redis = None
            self._stop.wait(POLL_INTERVAL_SEC)
            return

        for _stream, messages in batches or []:
            for entry_id, fields in messages:
                self._cursor = entry_id
                try:
                    event = wire.parse_event(fields)
                except ValueError as exc:
                    log.warning("Unusable VOICE:EVENT %s: %s", entry_id, exc)
                    continue
                self._ui_queue.put(("event", event))

    # --- UI drain ---

    def _drain_ui_queue(self) -> None:
        while True:
            try:
                message = self._ui_queue.get_nowait()
            except queue.Empty:
                return
            if message[0] == "event":
                self._apply_event(message[1])
            elif message[0] == "repos":
                self._apply_repos(message[1])

    def _apply_event(self, event: dict) -> None:
        kind, text = event["kind"], event["text"]
        if kind == wire.KIND_STATE:
            self._apply_state(text)
        elif kind == wire.KIND_PARTIAL:
            self._apply_partial(text)
        elif kind == wire.KIND_FINAL:
            self._apply_partial("")
            self._add_line(f"you: {text}", COLOR_YOU)
        elif kind == wire.KIND_ANSWER:
            self._add_line(text, COLOR_SAID)
        elif kind == wire.KIND_DISPATCH:
            self._add_line(f"dispatch {text}", COLOR_AMBER)
        elif kind == wire.KIND_ERROR:
            self._add_line(text, COLOR_RED)
        elif kind == wire.KIND_TARGET:
            tag = self._tag("repo_target")
            if dpg.does_item_exist(tag):
                dpg.set_value(tag, text)

    def _apply_state(self, state: str) -> None:
        if state not in STATE_COLORS:
            return
        self._state = state
        color = STATE_COLORS[state]
        light = self._tag("state_light")
        if dpg.does_item_exist(light):
            dpg.configure_item(light, fill=color, color=color)
        self._apply_status(state, color)
        self._label("talk_btn", "listen" if state == wire.STATE_OFF else "stop")

    def _label(self, suffix: str, label: str) -> None:
        tag = self._tag(suffix)
        if dpg.does_item_exist(tag):
            dpg.configure_item(tag, label=label)

    def _apply_status(self, text: str, color: tuple[int, int, int, int]) -> None:
        tag = self._tag("state_text")
        if dpg.does_item_exist(tag):
            dpg.set_value(tag, text)
            dpg.configure_item(tag, color=color)

    def _apply_partial(self, text: str) -> None:
        """Show in-flight recognition on one line that keeps being rewritten."""
        tag = self._tag("partial_line")
        parent = self._tag("transcript")
        if not text:
            if dpg.does_item_exist(tag):
                dpg.delete_item(tag)
            return
        if not dpg.does_item_exist(tag):
            if not dpg.does_item_exist(parent):
                return
            dpg.add_text(
                text, parent=parent, wrap=self._wrap, color=COLOR_DIM, tag=tag
            )
        else:
            dpg.set_value(tag, text)
        self._resize_transcript()

    def _apply_repos(self, repos: list[str]) -> None:
        tag = self._tag("repo_target")
        if not dpg.does_item_exist(tag):
            return
        if dpg.get_item_configuration(tag).get("items") == repos:
            return
        current = dpg.get_value(tag)
        dpg.configure_item(tag, items=repos)
        if not current and len(repos) == 1:
            dpg.set_value(tag, repos[0])

    def _add_line(self, text: str, color: tuple[int, int, int, int]) -> None:
        parent = self._tag("transcript")
        if not dpg.does_item_exist(parent):
            return
        self._line_seq += 1
        tag = self._tag(f"line_{self._line_seq}")
        # Keep the partial line last so new text does not appear above it.
        partial = self._tag("partial_line")
        before = partial if dpg.does_item_exist(partial) else 0
        dpg.add_text(
            text, parent=parent, wrap=self._wrap, color=color, tag=tag, before=before
        )
        self._lines.append(tag)
        while len(self._lines) > MAX_LINES:
            stale = self._lines.pop(0)
            if dpg.does_item_exist(stale):
                dpg.delete_item(stale)
        self._resize_transcript()

    def _resize_transcript(self) -> None:
        tag = self._tag("transcript")
        if not dpg.does_item_exist(tag):
            return
        rows = max(2, len(self._lines))
        height = rows * self._row_h
        if self._scroll_max is not None:
            height = min(height, self._scroll_max)
        dpg.configure_item(tag, height=height)


def build_ui(
    parent: str,
    *,
    tag_prefix: str,
    width: int = 460,
    height: int = 200,
) -> None:
    """Module-level builder for FeSpec / MegaDesk canvas hosting."""
    VoiceDeck().build_ui(parent, tag_prefix=tag_prefix, width=width, height=height)


def main() -> None:
    raise SystemExit("VoiceDeck FE is canvas chrome, not a standalone window.")


if __name__ == "__main__":
    main()
