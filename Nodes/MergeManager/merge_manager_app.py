"""MergeManager — show and open PRs from FINISHED:<REPO> items."""

from __future__ import annotations

import logging
import os
import queue
import threading
import webbrowser
from dataclasses import dataclass, field
from typing import Optional

import dearpygui.dearpygui as dpg
import redis
from megadesk_contracts import frame_pump, redis_connect, resolve_ephemeral_db, resolve_redis_url
from megadesk_contracts.wire.machine import (
    FINISHED_GROUP,
    FINISHED_PREFIX,
    parse_finished,
    repo_from_finished_key,
)
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from redis.exceptions import ResponseError
from redis.exceptions import TimeoutError as RedisTimeoutError

log = logging.getLogger("merge_manager")

POLL_INTERVAL_SEC = 2.0

# Keep live instances alive while their embed windows exist.
_LIVE: dict[str, "MergeManager"] = {}

COLOR_OK = (80, 200, 80, 255)
COLOR_ERR = (220, 70, 70, 255)
COLOR_DIM = (140, 140, 140, 255)
COLOR_INFO = (70, 140, 230, 255)


@dataclass
class FinishedItem:
    repo: str
    stream_key: str
    entry_id: str
    ticket_name: str
    ticket_id: str
    status: str
    pr_url: str
    row_tag: str = field(default="")


def connect_redis(redis_url: str | None = None) -> redis.Redis:
    url = resolve_redis_url(redis_url)
    client = redis_connect(
        url,
        db=resolve_ephemeral_db(url),
        socket_connect_timeout=2,
        socket_timeout=None,
    )
    try:
        client.ping()
    except (RedisConnectionError, RedisTimeoutError, OSError) as exc:
        raise SystemExit(
            f"Failed to connect to Redis at {url}. "
            "Start a local Redis server and retry."
        ) from exc
    return client


def short_pr_url(url: str) -> str:
    text = (url or "").strip()
    if not text:
        return ""
    if len(text) <= 48:
        return text
    return "…" + text[-47:]


def open_pr_url(url: str) -> tuple[bool, str]:
    text = (url or "").strip()
    if not text:
        return False, "No PR URL"
    try:
        opened = webbrowser.open(text)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    if not opened:
        return False, f"Could not open {short_pr_url(text)}"
    return True, f"Opened {short_pr_url(text)}"


class MergeManager:
    def __init__(self) -> None:
        self.redis_url = resolve_redis_url()
        self.consumer = os.environ.get(
            "MERGE_CONSUMER", f"merge-{os.getpid()}"
        )
        self._redis: Optional[redis.Redis] = None
        self._stop = threading.Event()
        self._poll_thread: Optional[threading.Thread] = None
        self._ui_queue: queue.Queue = queue.Queue()
        self._items: dict[str, FinishedItem] = {}
        self._dismiss_ready: Optional[str] = None
        self._lock = threading.Lock()
        self._root_tag = "primary"
        self._frame_registered = False

    def _tag(self, suffix: str) -> str:
        return f"{self._root_tag}::{suffix}"

    @staticmethod
    def item_key(repo: str, entry_id: str) -> str:
        return f"{repo}|{entry_id}"

    def _set_status(self, text: str, color: tuple[int, int, int, int] = COLOR_DIM) -> None:
        tag = self._tag("status_text")
        if dpg.does_item_exist(tag):
            dpg.set_value(tag, text)
            dpg.configure_item(tag, color=color)

    def _update_dismissal_tag(self) -> None:
        dismissal_tag = self._tag("dismissal_tag")
        dismissal_button = self._tag("dismissal_button")
        if not dpg.does_item_exist(dismissal_tag):
            return
        if self._dismiss_ready and self._dismiss_ready in self._items:
            item = self._items[self._dismiss_ready]
            dpg.set_value(dismissal_tag, f"Dismiss: {item.ticket_name}")
            dpg.configure_item(dismissal_button, show=True, enabled=True)
        else:
            dpg.set_value(dismissal_tag, "")
            dpg.configure_item(dismissal_button, show=True, enabled=False)

    def _ensure_group(self, stream_key: str) -> None:
        assert self._redis is not None
        try:
            self._redis.xgroup_create(
                stream_key,
                FINISHED_GROUP,
                id="0",
                mkstream=False,
            )
            log.info("Created consumer group %s on %s", FINISHED_GROUP, stream_key)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                if "no such key" in str(exc).lower():
                    return
                raise

    def _scan_finished_streams(self) -> list[str]:
        assert self._redis is not None
        keys: list[str] = []
        cursor: int | bytes = 0
        while True:
            cursor, batch = self._redis.scan(
                cursor=cursor, match=f"{FINISHED_PREFIX}*", count=100
            )
            for key in batch:
                if self._redis.type(key) == "stream":
                    keys.append(key)
            if cursor == 0 or cursor == "0":
                break
        return sorted(keys)

    def _read_stream_entries(
        self, stream_key: str
    ) -> list[tuple[str, dict[str, str]]]:
        """Read pending then new entries from a FINISHED stream consumer group."""
        assert self._redis is not None
        self._ensure_group(stream_key)
        entries: list[tuple[str, dict[str, str]]] = []
        for stream_id in ("0", ">"):
            try:
                results = self._redis.xreadgroup(
                    groupname=FINISHED_GROUP,
                    consumername=self.consumer,
                    streams={stream_key: stream_id},
                    count=64,
                )
            except ResponseError as exc:
                log.debug("xreadgroup %s: %s", stream_key, exc)
                continue
            if not results:
                continue
            for _name, messages in results:
                for message_id, fields in messages:
                    entries.append((message_id, fields))
        return entries

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            try:
                if self._redis is None:
                    self._redis = connect_redis(self.redis_url)
                for stream_key in self._scan_finished_streams():
                    try:
                        repo = repo_from_finished_key(stream_key)
                    except ValueError as exc:
                        log.error("%s", exc)
                        continue
                    for entry_id, fields in self._read_stream_entries(stream_key):
                        key = self.item_key(repo, entry_id)
                        with self._lock:
                            if key in self._items:
                                continue
                        try:
                            parsed = parse_finished(fields)
                        except ValueError as exc:
                            log.error(
                                "Bad FINISHED entry %s on %s: %s",
                                entry_id,
                                stream_key,
                                exc,
                            )
                            assert self._redis is not None
                            self._redis.xack(stream_key, FINISHED_GROUP, entry_id)
                            continue
                        item = FinishedItem(
                            repo=repo,
                            stream_key=stream_key,
                            entry_id=entry_id,
                            ticket_name=parsed["ticket_name"],
                            ticket_id=parsed["ticket_id"],
                            status=parsed["status"],
                            pr_url=parsed["pr_url"],
                        )
                        with self._lock:
                            self._items[key] = item
                        self._ui_queue.put(("add", key))
            except SystemExit:
                raise
            except RedisError as exc:
                log.warning("Redis poll error: %s", exc)
                self._redis = None
                self._ui_queue.put(("status", f"Redis error: {exc}", COLOR_ERR))
            except Exception:  # noqa: BLE001
                log.exception("Unhandled poll error")
            self._stop.wait(POLL_INTERVAL_SEC)

    def _drain_ui_queue(self) -> None:
        while True:
            try:
                msg = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            kind = msg[0]
            if kind == "add":
                self._add_row(msg[1])
            elif kind == "status":
                self._set_status(msg[1], msg[2])

    def _row_label(self, item: FinishedItem) -> str:
        bits = [item.ticket_name, item.status]
        short = short_pr_url(item.pr_url)
        if short:
            bits.append(short)
        return "  —  ".join(bits)

    def _add_row(self, key: str) -> None:
        item = self._items.get(key)
        table = self._tag("ticket_table")
        if item is None or not dpg.does_item_exist(table):
            return
        if item.row_tag and dpg.does_item_exist(item.row_tag):
            return

        row_tag = self._tag(f"row::{key}")
        item.row_tag = row_tag
        with dpg.table_row(parent=table, tag=row_tag):
            dpg.add_button(
                label="open PR",
                width=70,
                tag=self._tag(f"open_pr::{key}"),
                callback=self._on_open_pr,
                user_data=key,
                enabled=bool(item.pr_url),
            )
            dpg.add_text(self._row_label(item), tag=self._tag(f"name::{key}"))
            dpg.add_button(
                label="dismiss",
                width=60,
                tag=self._tag(f"dismiss::{key}"),
                callback=self._on_dismiss_row,
                user_data=key,
            )

        self._dismiss_ready = key
        status_bits = [item.status]
        short = short_pr_url(item.pr_url)
        if short:
            status_bits.append(short)
        self._set_status(
            f"{item.ticket_name}: {' '.join(status_bits)}",
            COLOR_INFO,
        )
        self._update_dismissal_tag()

    def _on_open_pr(self, _sender: str, _app_data: object, key: str) -> None:
        item = self._items.get(key)
        if item is None:
            return
        ok, msg = open_pr_url(item.pr_url)
        self._set_status(msg, COLOR_OK if ok else COLOR_ERR)

    def _ack_and_drop(self, key: str) -> None:
        item = self._items.pop(key, None)
        if item is None:
            return
        try:
            if self._redis is None:
                self._redis = connect_redis(self.redis_url)
            self._redis.xack(item.stream_key, FINISHED_GROUP, item.entry_id)
            self._redis.xdel(item.stream_key, item.entry_id)
        except RedisError as exc:
            log.warning("Failed to ack/delete %s: %s", item.entry_id, exc)
        if item.row_tag and dpg.does_item_exist(item.row_tag):
            dpg.delete_item(item.row_tag)
        if self._dismiss_ready == key:
            self._dismiss_ready = next(iter(self._items), None)
        self._update_dismissal_tag()

    def _on_dismiss_row(self, _sender: str, _app_data: object, key: str) -> None:
        item = self._items.get(key)
        name = item.ticket_name if item else key
        self._ack_and_drop(key)
        self._set_status(f"Dismissed {name}", COLOR_OK)

    def _on_dismiss_header(self) -> None:
        if not self._dismiss_ready:
            self._set_status("Nothing ready to dismiss", COLOR_DIM)
            return
        self._on_dismiss_row("", None, self._dismiss_ready)

    def build_ui(
        self,
        parent: str,
        *,
        tag_prefix: str,
        width: int = 640,
        height: int = 220,
    ) -> None:
        """Fill the host content parent with MergeManager widgets."""
        self._root_tag = tag_prefix
        _ = width, height

        with dpg.group(parent=parent):
            with dpg.group(horizontal=True):
                dpg.add_text("", tag=self._tag("status_text"), color=COLOR_DIM)
                dpg.add_spacer(width=8)
                dpg.add_text(
                    "",
                    tag=self._tag("dismissal_tag"),
                    color=COLOR_DIM,
                )
                dpg.add_button(
                    label="Dismiss",
                    width=60,
                    tag=self._tag("dismissal_button"),
                    callback=lambda: self._on_dismiss_header(),
                    enabled=False,
                )

            dpg.add_spacer(height=2)

            with dpg.table(
                tag=self._tag("ticket_table"),
                header_row=False,
                borders_innerH=True,
                borders_outerH=True,
                borders_innerV=False,
                borders_outerV=True,
                resizable=True,
                policy=dpg.mvTable_SizingStretchProp,
                row_background=True,
            ):
                dpg.add_table_column(init_width_or_weight=0.15)
                dpg.add_table_column(init_width_or_weight=0.70)
                dpg.add_table_column(init_width_or_weight=0.15)

        dpg.set_item_user_data(parent, self.shutdown)
        self._start_services()
        _LIVE[tag_prefix] = self
        self._set_status(
            f"Connected — watching {FINISHED_PREFIX}* streams", COLOR_INFO
        )
        self._update_dismissal_tag()

    def _start_services(self) -> None:
        if self._poll_thread and self._poll_thread.is_alive():
            return
        if self._redis is None:
            try:
                self._redis = connect_redis(self.redis_url)
            except SystemExit:
                self._redis = None
        self._stop.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop, name="finished-poll", daemon=True
        )
        self._poll_thread.start()
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
        if self._poll_thread is not None:
            self._poll_thread.join(timeout=2.0)
            self._poll_thread = None
        _LIVE.pop(self._root_tag, None)


def build_ui(
    parent: str,
    *,
    tag_prefix: str,
    width: int = 640,
    height: int = 220,
) -> None:
    """Module-level builder for FeSpec / MegaDesk canvas hosting."""
    MergeManager().build_ui(
        parent,
        tag_prefix=tag_prefix,
        width=width,
        height=height,
    )


def main() -> None:
    raise SystemExit(
        "MergeManager FE is canvas-only. Drop it from the MegaDesk Catalog."
    )


if __name__ == "__main__":
    main()
