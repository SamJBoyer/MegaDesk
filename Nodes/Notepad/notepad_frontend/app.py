"""Notepad — a compact tabbed pad hosted as one canvas node.

Tabs are documents. The body is one multiline editor. Save writes ``.txt``
files and, when a GitHub repo is attached, git-includes them.
"""

from __future__ import annotations

import logging
import time
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
from megadesk_contracts.repo import CloneError
from megadesk_contracts.wire import notepad as wire
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError

from notepad_frontend.pad import (
    Pad,
    PadError,
    apply_command,
    default_pad_root,
    safe_note_name,
)

log = logging.getLogger("notepad.fe")

POLL_INTERVAL_SEC = 0.4
COMMAND_BATCH = 32
PARAM_GIT_URL = "GIT_URL"

COLOR_OK = (80, 200, 80, 255)
COLOR_ERR = (220, 70, 70, 255)
COLOR_DIM = (140, 140, 140, 255)

_LIVE: dict[str, "Notepad"] = {}


class Notepad:
    def __init__(self, parameters: Optional[Mapping[str, str]] = None) -> None:
        values = coerce_parameters(parameters)
        self.pad = Pad()
        self._git_url = values.get(PARAM_GIT_URL, "").strip()
        self._root_tag = "notepad"
        self._frame_registered = False
        self._last_poll = 0.0
        self._command_cursor = "$"
        self._redis: Optional[redis.Redis] = None
        self.redis_url = resolve_redis_url()
        self._status = ""
        self._status_color = COLOR_DIM
        self._rebuilding = False

    def _tag(self, suffix: str) -> str:
        return f"{self._root_tag}::{suffix}"

    def _tab_tag(self, name: str) -> str:
        return self._tag(f"tab_{name}")

    def _connect_redis(self) -> Optional[redis.Redis]:
        if self._redis is not None:
            try:
                self._redis.ping()
                return self._redis
            except (RedisConnectionError, RedisTimeoutError, OSError, RedisError):
                self._redis = None
        try:
            client = redis_connect(
                self.redis_url,
                db=resolve_ephemeral_db(self.redis_url),
                socket_connect_timeout=1.5,
                socket_timeout=2.0,
            )
            client.ping()
            self._redis = client
            return client
        except (RedisConnectionError, RedisTimeoutError, OSError, RedisError):
            self._redis = None
            return None

    def _last_id(self, stream: str) -> str:
        client = self._redis
        if client is None:
            return "0-0"
        try:
            newest = client.xrevrange(stream, count=1)
        except RedisError:
            return "0-0"
        return newest[0][0] if newest else "0-0"

    def _flush_body(self) -> None:
        body = self._tag("body")
        if not dpg.does_item_exist(body):
            return
        if not self.pad.current:
            return
        self.pad.set_text(str(dpg.get_value(body) or ""))

    def _refresh_tabs(self) -> None:
        bar = self._tag("tabs")
        if not dpg.does_item_exist(bar):
            return
        self._rebuilding = True
        try:
            dpg.delete_item(bar, children_only=True)
            for name in self.pad.names():
                dpg.add_tab(label=name, parent=bar, tag=self._tab_tag(name))
            if self.pad.current and dpg.does_item_exist(self._tab_tag(self.pad.current)):
                dpg.set_value(bar, self._tab_tag(self.pad.current))
        finally:
            self._rebuilding = False
        self._refresh_body()

    def _refresh_body(self) -> None:
        body = self._tag("body")
        if not dpg.does_item_exist(body):
            return
        doc = self.pad.current_document()
        dpg.set_value(body, doc.text if doc is not None else "")

    def _set_status(self, text: str, color: tuple[int, int, int, int] = COLOR_DIM) -> None:
        self._status = text
        self._status_color = color
        tag = self._tag("status_text")
        if dpg.does_item_exist(tag):
            dpg.set_value(tag, text)
            dpg.configure_item(tag, color=color)

    def new_document(self, title: str = "") -> str:
        self._flush_body()
        doc = self.pad.create(title)
        self._refresh_tabs()
        name_tag = self._tag("new_name")
        if dpg.does_item_exist(name_tag):
            dpg.set_value(name_tag, "")
        return doc.name

    def add_text(self, text: str, title: str = "") -> str:
        self._flush_body()
        doc = self.pad.append(text, title)
        if title and doc.name != self.pad.current:
            self.pad.current = doc.name
        self._refresh_tabs()
        return doc.name

    def switch_document(self, title: str) -> str:
        self._flush_body()
        if safe_note_name(title) == self.pad.current:
            self._refresh_body()
            return self.pad.current
        doc = self.pad.switch(title)
        self._refresh_tabs()
        return doc.name

    def apply_command(self, fields: Mapping[str, str]) -> None:
        self._flush_body()
        apply_command(self.pad, fields)
        self._refresh_tabs()

    def attach_repo(self, url: str) -> None:
        self._flush_body()
        self._git_url = url.strip()
        if not self._git_url:
            return
        try:
            dest = self.pad.attach_repo(self._git_url, root=default_pad_root())
        except (PadError, CloneError, ValueError) as exc:
            self._set_status(str(exc), COLOR_ERR)
            return
        self._refresh_tabs()
        self._set_status(dest.name, COLOR_OK)

    def save(self) -> None:
        self._flush_body()
        if not self.pad.current and not self.pad.names():
            self._set_status("empty", COLOR_DIM)
            return
        try:
            written = self.pad.save()
        except (PadError, OSError) as exc:
            self._set_status(str(exc), COLOR_ERR)
            return
        self._set_status(f"{len(written)}", COLOR_OK)

    def _on_new(self, _sender=None, app_data=None, _user_data=None) -> None:
        title = ""
        name_tag = self._tag("new_name")
        if dpg.does_item_exist(name_tag):
            title = str(dpg.get_value(name_tag) or "").strip()
        if not title and app_data is not None and not isinstance(app_data, (int, float)):
            title = str(app_data).strip()
        try:
            self.new_document(title)
        except PadError as exc:
            self._set_status(str(exc), COLOR_ERR)

    def _on_save(self, *_args) -> None:
        self.save()

    def _on_url(self, _sender=None, app_data=None, _user_data=None) -> None:
        url_tag = self._tag("git_url")
        url = str(app_data or "")
        if dpg.does_item_exist(url_tag):
            url = str(dpg.get_value(url_tag) or "")
        self.attach_repo(url)

    def _on_tab(self, _sender=None, app_data=None, _user_data=None) -> None:
        if self._rebuilding:
            return
        selected = str(app_data or "")
        prefix = f"{self._root_tag}::tab_"
        if not selected.startswith(prefix):
            return
        name = selected[len(prefix) :]
        try:
            self.switch_document(name)
        except PadError as exc:
            self._set_status(str(exc), COLOR_ERR)

    def _on_body(self, _sender=None, app_data=None, _user_data=None) -> None:
        if not self.pad.current:
            title_tag = self._tag("new_name")
            title = (
                str(dpg.get_value(title_tag) or "").strip()
                if dpg.does_item_exist(title_tag)
                else ""
            )
            self.pad.create(title)
            self._refresh_tabs()
        text = app_data if app_data is not None else ""
        body = self._tag("body")
        if dpg.does_item_exist(body) and app_data is None:
            text = dpg.get_value(body)
        self.pad.set_text(str(text or ""))

    def _poll(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and (now - self._last_poll) < POLL_INTERVAL_SEC:
            return
        self._last_poll = now
        client = self._connect_redis()
        if client is None:
            return
        cursor = self._command_cursor
        if cursor == "$":
            cursor = self._last_id(wire.COMMAND_STREAM)
            self._command_cursor = cursor
        try:
            batches = client.xread({wire.COMMAND_STREAM: cursor}, count=COMMAND_BATCH)
        except RedisError:
            self._redis = None
            return
        for _stream, messages in batches or []:
            for entry_id, fields in messages:
                self._command_cursor = entry_id
                try:
                    parsed = wire.parse_command(fields)
                except ValueError as exc:
                    log.warning("Unusable NOTEPAD:COMMAND %s: %s", entry_id, exc)
                    continue
                try:
                    self.apply_command(parsed)
                except PadError as exc:
                    self._set_status(str(exc), COLOR_ERR)

    def build_ui(
        self,
        parent: str,
        *,
        tag_prefix: str,
        width: int = 300,
        height: int = 220,
    ) -> None:
        self._root_tag = tag_prefix
        body_h = max(80, int(height) - 72)

        with dpg.group(parent=parent):
            with dpg.group(horizontal=True):
                dpg.add_input_text(
                    tag=self._tag("git_url"),
                    default_value=self._git_url,
                    width=-40,
                    hint="https://github.com/owner/notes",
                    callback=self._on_url,
                    on_enter=True,
                )
                dpg.add_button(
                    label="save",
                    tag=self._tag("save_btn"),
                    width=36,
                    callback=self._on_save,
                )
            with dpg.group(horizontal=True):
                dpg.add_input_text(
                    tag=self._tag("new_name"),
                    width=-24,
                    hint="title",
                    callback=self._on_new,
                    on_enter=True,
                )
                dpg.add_button(
                    label="+",
                    tag=self._tag("new_btn"),
                    width=20,
                    callback=self._on_new,
                )
            dpg.add_tab_bar(tag=self._tag("tabs"), callback=self._on_tab)
            dpg.add_input_text(
                tag=self._tag("body"),
                default_value="",
                multiline=True,
                width=-1,
                height=body_h,
                callback=self._on_body,
            )
            dpg.add_text("", tag=self._tag("status_text"), color=COLOR_DIM)

        dpg.set_item_user_data(parent, self.shutdown)
        if self._git_url:
            self.attach_repo(self._git_url)
        if not self._frame_registered:
            frame_pump.register(self._on_frame)
            self._frame_registered = True
        _LIVE[tag_prefix] = self
        _ = width

    def _on_frame(self) -> None:
        if not dpg.does_item_exist(self._root_tag):
            return
        self._poll()

    def shutdown(self) -> None:
        if self._frame_registered:
            frame_pump.unregister(self._on_frame)
            self._frame_registered = False
        self._redis = None
        _LIVE.pop(self._root_tag, None)


def build_ui(
    parent: str,
    *,
    tag_prefix: str,
    width: int = 300,
    height: int = 220,
    parameters: Optional[Mapping[str, str]] = None,
) -> None:
    Notepad(parameters).build_ui(
        parent,
        tag_prefix=tag_prefix,
        width=width,
        height=height,
    )


def read_parameters(tag_prefix: str) -> dict[str, str]:
    url_tag = f"{tag_prefix}::git_url"
    if not dpg.does_item_exist(url_tag):
        return {}
    return {PARAM_GIT_URL: str(dpg.get_value(url_tag) or "").strip()}
