"""Notepad FE — a compact pad of tabbed text files a voice agent can write to.

Notes persist as ``.txt`` files. Point the URL at a GitHub repo to keep them
inside that clone; ``git`` stages them so they can be committed with the rest
of the tree. VoiceDeck publishes ``NOTEPAD:CMD``; this FE applies those verbs
on the frame pump.
"""

from __future__ import annotations

from typing import Mapping, Optional

import dearpygui.dearpygui as dpg
import redis
from megadesk_contracts import (
    coerce_parameters,
    frame_pump,
    redis_connect,
    resolve_ephemeral_db,
    resolve_redis_url,
)
from megadesk_contracts.wire import notepad as wire
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError

from notepad_frontend.pad import Pad, PadError, safe_title

PARAM_GIT_URL = "GIT_URL"
CMD_BATCH = 32

_LIVE: dict[str, "Notepad"] = {}


class Notepad:
    """One hosted pad: its files, its tabs, and the Redis commands it applies."""

    def __init__(self, parameters: Optional[Mapping[str, str]] = None) -> None:
        values = coerce_parameters(parameters)
        self.pad = Pad()
        self._git_url = values.get(PARAM_GIT_URL, "").strip()
        self._root_tag = "notepad"
        self._redis: Optional[redis.Redis] = None
        self._cursor = "$"
        self._frame_registered = False
        if self._git_url:
            try:
                self.pad.attach_repo(self._git_url)
            except PadError:
                self.pad.load()
        else:
            self.pad.load()
        if not self.pad.notes:
            self.pad.create("note")

    def _tag(self, suffix: str) -> str:
        return f"{self._root_tag}::{suffix}"

    def _connect_redis(self) -> Optional[redis.Redis]:
        if self._redis is not None:
            try:
                self._redis.ping()
                return self._redis
            except (RedisConnectionError, RedisTimeoutError, OSError, RedisError):
                self._redis = None
        try:
            url = resolve_redis_url()
            client = redis_connect(
                url,
                db=resolve_ephemeral_db(url),
                socket_connect_timeout=1.5,
                socket_timeout=2.0,
            )
            client.ping()
            self._redis = client
            self._cursor = self._last_id(client)
            return client
        except (RedisConnectionError, RedisTimeoutError, OSError, RedisError, ValueError):
            self._redis = None
            return None

    def _last_id(self, client: redis.Redis) -> str:
        try:
            newest = client.xrevrange(wire.CMD_STREAM, count=1)
        except RedisError:
            return "$"
        if not newest:
            return "0-0"
        return str(newest[0][0])

    # --- document verbs (mouse-free, so tests and Redis share them) ---

    def create_note(self, title: str, text: str = "") -> str:
        note = self.pad.create(title, text)
        self._persist()
        self._refresh()
        return note.title

    def add_text(self, text: str, title: str = "") -> str:
        note = self.pad.append(text, title)
        self._persist()
        self._refresh()
        return note.title

    def switch_note(self, title: str) -> str:
        note = self.pad.switch(title)
        self._persist()
        self._refresh()
        return note.title

    def include(self) -> list[str]:
        self._flush_body()
        return self.pad.git_include()

    def apply_command(self, command: dict[str, str]) -> str:
        self._flush_body()
        note = self.pad.apply(command)
        self._persist()
        self._refresh()
        return note.title

    # --- persistence ---

    def _persist(self) -> None:
        self.pad.save()

    def _flush_body(self) -> None:
        tag = self._tag("body")
        if not dpg.does_item_exist(tag) or not self.pad.current:
            return
        self.pad.set_text(self.pad.current, dpg.get_value(tag) or "")

    # --- UI ---

    def build_ui(
        self,
        parent: str,
        *,
        tag_prefix: str,
        width: int = 280,
        height: int = 200,
    ) -> None:
        self._root_tag = tag_prefix
        body_h = max(72, int(height) - 56)

        with dpg.group(parent=parent):
            with dpg.group(horizontal=True):
                dpg.add_input_text(
                    tag=self._tag("git_url"),
                    default_value=self._git_url,
                    width=-52,
                    hint="https://github.com/owner/repo",
                    callback=self._on_url,
                    on_enter=True,
                )
                dpg.add_button(
                    label="+",
                    width=22,
                    tag=self._tag("new_btn"),
                    callback=self._on_new,
                )
                dpg.add_button(
                    label="git",
                    width=26,
                    tag=self._tag("include_btn"),
                    callback=self._on_include,
                )
            with dpg.group(horizontal=True, tag=self._tag("tabs")):
                pass
            dpg.add_input_text(
                tag=self._tag("body"),
                multiline=True,
                width=max(160, int(width) - 16),
                height=body_h,
                callback=self._on_body,
            )

        dpg.set_item_user_data(parent, self.shutdown)
        _LIVE[tag_prefix] = self
        self._connect_redis()
        self._refresh()
        if not self._frame_registered:
            frame_pump.register(self._on_frame)
            self._frame_registered = True

    def _refresh(self) -> None:
        self._refresh_tabs()
        self._show_current()

    def _refresh_tabs(self) -> None:
        bar = self._tag("tabs")
        if not dpg.does_item_exist(bar):
            return
        dpg.delete_item(bar, children_only=True)
        for title in self.pad.titles():
            dpg.add_button(
                parent=bar,
                label=title,
                small=True,
                tag=self._tag(f"tab_{safe_title(title)}"),
                user_data=title,
                callback=self._on_tab,
            )

    def _show_current(self) -> None:
        tag = self._tag("body")
        if not dpg.does_item_exist(tag):
            return
        note = self.pad.note()
        dpg.set_value(tag, note.text if note is not None else "")

    def _on_new(self, sender=None, app_data=None, user_data=None) -> None:
        self._flush_body()
        self.create_note(self.pad.next_title())

    def _on_tab(self, sender=None, app_data=None, user_data=None) -> None:
        title = str(user_data or "").strip()
        if not title:
            return
        self._flush_body()
        self.switch_note(title)

    def _on_body(self, sender=None, app_data=None, user_data=None) -> None:
        self._flush_body()
        self._persist()

    def _on_include(self, sender=None, app_data=None, user_data=None) -> None:
        try:
            self.include()
        except PadError:
            return

    def _on_url(self, sender=None, app_data=None, user_data=None) -> None:
        tag = self._tag("git_url")
        url = (dpg.get_value(tag) or "").strip() if dpg.does_item_exist(tag) else ""
        self._git_url = url
        if not url:
            return
        self._flush_body()
        try:
            self.pad.attach_repo(url)
        except PadError:
            return
        if not self.pad.notes:
            self.pad.create("note")
        self._refresh()

    def _on_frame(self) -> None:
        if not dpg.does_item_exist(self._root_tag):
            return
        self._drain_commands()

    def _drain_commands(self) -> int:
        client = self._connect_redis()
        if client is None:
            return 0
        try:
            reply = client.xread(
                {wire.CMD_STREAM: self._cursor}, count=CMD_BATCH, block=0
            )
        except RedisError:
            self._redis = None
            return 0
        applied = 0
        for _stream, items in reply or []:
            for entry_id, fields in items:
                self._cursor = str(entry_id)
                try:
                    command = wire.parse_command(fields)
                except ValueError:
                    continue
                self.apply_command(command)
                applied += 1
        return applied

    def shutdown(self) -> None:
        self._flush_body()
        try:
            self._persist()
        except OSError:
            pass
        if self._frame_registered:
            frame_pump.unregister(self._on_frame)
            self._frame_registered = False
        self._redis = None
        _LIVE.pop(self._root_tag, None)


def build_ui(
    parent: str,
    *,
    tag_prefix: str,
    width: int = 280,
    height: int = 200,
    parameters: Optional[Mapping[str, str]] = None,
) -> None:
    Notepad(parameters).build_ui(
        parent, tag_prefix=tag_prefix, width=width, height=height
    )


def read_parameters(tag_prefix: str) -> dict[str, str]:
    url_tag = f"{tag_prefix}::git_url"
    if not dpg.does_item_exist(url_tag):
        instance = _LIVE.get(tag_prefix)
        url = instance._git_url if instance is not None else ""
        return {PARAM_GIT_URL: url}
    return {PARAM_GIT_URL: (dpg.get_value(url_tag) or "").strip()}
