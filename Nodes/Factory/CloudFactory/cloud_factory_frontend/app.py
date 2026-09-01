"""CloudFactory canvas monitor — queued CLOUDORDERs and live agents.

Each queued order carries its own status lamp: blinks blue while that ticket
is still booting, red if it died, green otherwise.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import dearpygui.dearpygui as dpg
import redis
from megadesk_contracts import (
    frame_pump,
    redis_connect,
    resolve_ephemeral_db,
    resolve_persistent_db,
    resolve_redis_url,
)
from megadesk_contracts.wire import cloud as wire
from megadesk_contracts.wire.factory import TERMINAL_STATUSES, is_terminal
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError

POLL_INTERVAL_SEC = 1.5
CLOUDORDER_RECENT = 12
FINISHED_RECENT = 12
HASH_SCAN_COUNT = 100
LAMP_BLINK_SEC = 0.45

COLOR_OK = (80, 200, 80, 255)
COLOR_ERR = (220, 70, 70, 255)
COLOR_DIM = (140, 140, 140, 255)
COLOR_BLUE = (70, 140, 230, 255)
COLOR_BLUE_DIM = (28, 58, 110, 255)

LAMP_ERROR = True
LAMP_STARTING = "starting"
LAMP_OK = False

QUEUE_ROW_H = 18
QUEUE_MIN_VISIBLE = 2
QUEUE_MAX_VISIBLE = 6
QUEUE_LAMP_W = 14

_LIVE: dict[str, "CloudFactoryFE"] = {}


def lamp_for_order(*, status: str, pr_url: str = "", order_id: str = "") -> object:
    """Per-ticket lamp: red if this order died, blink if it is still booting."""
    if status in {wire.STATUS_ERROR, wire.STATUS_STARTUP_ERROR}:
        return LAMP_ERROR
    if pr_url or status in {
        wire.STATUS_CANCELLED,
        wire.STATUS_FINISHED,
        wire.STATUS_RUNNING,
    }:
        return LAMP_OK
    if order_id:
        return LAMP_STARTING
    return LAMP_OK


def _row_key(row: "OrderRow") -> str:
    return row.order_id or row.entry_id


@dataclass
class OrderRow:
    entry_id: str
    order_id: str
    title: str
    model: str
    status: str
    label: str
    lamp: object = LAMP_OK


@dataclass
class LiveRow:
    agent_id: str
    order_id: str
    title: str
    status: str
    label: str


class CloudFactoryFE:
    """CloudFactory monitor: queue (per-order lamp), live agents, reject."""

    def __init__(self) -> None:
        self.redis_url = resolve_redis_url()
        self._root_tag = "primary"
        self._frame_registered = False
        self._last_poll = 0.0
        self._redis: Optional[redis.Redis] = None
        self._persistent: Optional[redis.Redis] = None
        self._orders: list[OrderRow] = []
        self._live: list[LiveRow] = []
        self._selected_key = ""
        self._queue_keys: list[str] | None = None
        self._col_w = 176

    def _tag(self, suffix: str) -> str:
        return f"{self._root_tag}::{suffix}"

    def _connect_redis(self) -> bool:
        if self._redis is not None and self._persistent is not None:
            try:
                self._redis.ping()
                self._persistent.ping()
                return True
            except (RedisConnectionError, RedisTimeoutError, OSError, RedisError):
                self._redis = None
                self._persistent = None
        try:
            client = redis_connect(
                self.redis_url,
                db=resolve_ephemeral_db(self.redis_url),
                socket_connect_timeout=1.5,
                socket_timeout=2.0,
            )
            client.ping()
            persistent = redis_connect(
                self.redis_url,
                db=resolve_persistent_db(self.redis_url),
                socket_connect_timeout=1.5,
                socket_timeout=2.0,
            )
            persistent.ping()
            self._redis = client
            self._persistent = persistent
            return True
        except (RedisConnectionError, RedisTimeoutError, OSError, RedisError, ValueError):
            self._redis = None
            self._persistent = None
            return False

    def build_ui(
        self,
        parent: str,
        *,
        tag_prefix: str,
        width: int = 420,
        height: int = 160,
    ) -> None:
        self._root_tag = tag_prefix
        _ = width, height
        self._col_w = 176
        col_w = self._col_w

        theme = self._tag("queue_theme")
        if not dpg.does_item_exist(theme):
            with dpg.theme(tag=theme):
                with dpg.theme_component(dpg.mvChildWindow):
                    dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 2, 2)
                    dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 2, 1)
                with dpg.theme_component(dpg.mvButton):
                    dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 2, 1)
                    dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 2)

        with dpg.group(parent=parent):
            with dpg.group(horizontal=True):
                with dpg.group():
                    dpg.add_text("Queue", color=COLOR_DIM)
                    dpg.add_child_window(
                        tag=self._tag("queue_list"),
                        width=col_w,
                        height=self._queue_height(),
                        border=True,
                    )
                    dpg.bind_item_theme(self._tag("queue_list"), theme)
                    dpg.add_button(
                        label="reject",
                        width=54,
                        height=20,
                        tag=self._tag("reject_btn"),
                        callback=self._on_reject,
                    )
                with dpg.group():
                    dpg.add_text("Live", color=COLOR_DIM)
                    dpg.add_listbox(
                        items=["(loading…)"],
                        tag=self._tag("live_list"),
                        num_items=2,
                        width=-1,
                    )

        dpg.set_item_user_data(parent, self.shutdown)
        self._poll(force=True)
        if not self._frame_registered:
            frame_pump.register(self._on_frame)
            self._frame_registered = True
        _LIVE[tag_prefix] = self

    def _on_frame(self) -> None:
        if not dpg.does_item_exist(self._root_tag):
            return
        self._poll()
        if any(row.lamp == LAMP_STARTING for row in self._orders):
            self._paint_lamps()

    def shutdown(self) -> None:
        if self._frame_registered:
            frame_pump.unregister(self._on_frame)
            self._frame_registered = False
        theme = self._tag("queue_theme")
        if dpg.does_item_exist(theme):
            dpg.delete_item(theme)
        self._redis = None
        self._persistent = None
        _LIVE.pop(self._root_tag, None)

    def _scan_runs(self, persistent: redis.Redis) -> dict[str, dict[str, str]]:
        runs: dict[str, dict[str, str]] = {}
        try:
            for key in persistent.scan_iter(
                match=f"{wire.CLOUDRUN_PREFIX}*", count=HASH_SCAN_COUNT
            ):
                try:
                    agent_id = wire.agent_id_from_key(key)
                    parsed = wire.parse_cloudrun(persistent.hgetall(key))
                except ValueError:
                    continue
                runs[agent_id] = parsed
        except RedisError:
            return {}
        return runs

    def _finished_by_order(self, client: redis.Redis) -> dict[str, dict[str, str]]:
        try:
            entries = client.xrevrange(wire.CLOUDFINISHED_STREAM, count=FINISHED_RECENT)
        except RedisError:
            return {}
        by_order: dict[str, dict[str, str]] = {}
        for _entry_id, fields in entries:
            try:
                parsed = wire.parse_cloudfinished(fields)
            except ValueError:
                continue
            by_order.setdefault(parsed["order_id"], parsed)
        return by_order

    def _recent_orders(
        self,
        client: redis.Redis,
        runs_by_order: dict[str, dict[str, str]],
        finished_by_order: dict[str, dict[str, str]],
    ) -> list[OrderRow]:
        try:
            entries = client.xrevrange(wire.CLOUDORDER_STREAM, count=CLOUDORDER_RECENT)
        except RedisError:
            return []
        rows: list[OrderRow] = []
        for entry_id, fields in entries:
            try:
                parsed = wire.parse_cloudorder(fields)
            except ValueError:
                rows.append(
                    OrderRow(
                        entry_id=entry_id,
                        order_id="",
                        title="?",
                        model="",
                        status="",
                        label=f"{entry_id}  (unparseable)",
                    )
                )
                continue
            order_id = parsed["order_id"]
            run = runs_by_order.get(order_id) or {}
            done = finished_by_order.get(order_id) or {}
            status = str(run.get("status") or done.get("status") or "")
            pr_url = str(run.get("pr_url") or done.get("pr_url") or "")
            lamp = lamp_for_order(status=status, pr_url=pr_url, order_id=order_id)
            # Handed off to GitHub: do not show MegaDesk finished (or running).
            # Failures on CLOUDFINISHED stay visible.
            if pr_url and status not in {
                wire.STATUS_ERROR,
                wire.STATUS_CANCELLED,
                wire.STATUS_STARTUP_ERROR,
            }:
                status = ""
            short_id = entry_id if len(entry_id) <= 14 else entry_id[:14]
            label = f"{short_id}  {parsed['title']}  {parsed['model']}"
            if status:
                label = f"{label}  {status}"
            rows.append(
                OrderRow(
                    entry_id=entry_id,
                    order_id=order_id,
                    title=parsed["title"],
                    model=parsed["model"],
                    status=status,
                    label=label,
                    lamp=lamp,
                )
            )
        return rows

    def _live_from_runs(self, runs: dict[str, dict[str, str]]) -> list[LiveRow]:
        rows: list[LiveRow] = []
        for agent_id, run in runs.items():
            status = run["status"]
            if is_terminal(status) or run["pr_url"]:
                continue
            short = agent_id if len(agent_id) <= 8 else agent_id[:8]
            rows.append(
                LiveRow(
                    agent_id=agent_id,
                    order_id=run["order_id"],
                    title=run["title"],
                    status=status,
                    label=f"{short}…  {status}  {run['title']}",
                )
            )
        rows.sort(key=lambda r: (r.status, r.agent_id))
        return rows

    def _poll(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and (now - self._last_poll) < POLL_INTERVAL_SEC:
            return
        self._last_poll = now

        if not self._connect_redis():
            self._orders = []
            self._live = []
            self._refresh_widgets()
            return

        client = self._redis
        persistent = self._persistent
        assert client is not None and persistent is not None

        try:
            runs = self._scan_runs(persistent)
            finished = self._finished_by_order(client)
            runs_by_order = {run["order_id"]: run for run in runs.values()}
            self._orders = self._recent_orders(client, runs_by_order, finished)
            self._live = self._live_from_runs(runs)
        except RedisError:
            self._redis = None
            self._persistent = None

        if self._selected_key and not any(
            _row_key(order) == self._selected_key for order in self._orders
        ):
            self._selected_key = ""

        self._refresh_widgets()

    def _lamp_color(self, state: object) -> tuple[int, int, int, int]:
        if state is LAMP_ERROR:
            return COLOR_ERR
        if state == LAMP_STARTING:
            on = int(time.monotonic() / LAMP_BLINK_SEC) % 2 == 0
            return COLOR_BLUE if on else COLOR_BLUE_DIM
        return COLOR_OK

    def _queue_height(self) -> int:
        n = len(self._orders)
        visible = max(QUEUE_MIN_VISIBLE, min(n if n else QUEUE_MIN_VISIBLE, QUEUE_MAX_VISIBLE))
        return 4 + visible * QUEUE_ROW_H + max(0, visible - 1)

    def _paint_lamps(self) -> None:
        for row in self._orders:
            lamp = self._tag(f"queue_lamp_{_row_key(row)}")
            if not dpg.does_item_exist(lamp):
                continue
            color = self._lamp_color(row.lamp)
            dpg.configure_item(lamp, fill=color, color=color)
            dpg.set_item_user_data(lamp, row.lamp)

    def _rebuild_queue_rows(self) -> None:
        parent = self._tag("queue_list")
        if not dpg.does_item_exist(parent):
            return
        dpg.delete_item(parent, children_only=True)
        dpg.configure_item(parent, height=self._queue_height())
        if not self._orders:
            dpg.add_text("(no CLOUDORDER entries)", parent=parent, color=COLOR_DIM)
            return
        for row in self._orders:
            key = _row_key(row)
            with dpg.group(parent=parent, horizontal=True):
                with dpg.drawlist(width=QUEUE_LAMP_W, height=QUEUE_ROW_H - 2):
                    dpg.draw_circle(
                        (QUEUE_LAMP_W // 2, (QUEUE_ROW_H - 2) // 2),
                        5,
                        fill=COLOR_OK,
                        color=COLOR_OK,
                        tag=self._tag(f"queue_lamp_{key}"),
                    )
                dpg.add_button(
                    label=row.label,
                    tag=self._tag(f"queue_item_{key}"),
                    width=max(40, self._col_w - QUEUE_LAMP_W - 16),
                    height=QUEUE_ROW_H - 2,
                    small=True,
                    callback=self._on_select_order,
                    user_data=key,
                )

    def _refresh_queue(self) -> None:
        keys = [_row_key(row) for row in self._orders]
        if keys != self._queue_keys:
            self._rebuild_queue_rows()
            self._queue_keys = keys
        else:
            for row in self._orders:
                item = self._tag(f"queue_item_{_row_key(row)}")
                if dpg.does_item_exist(item):
                    dpg.configure_item(item, label=row.label)
        self._paint_lamps()
        if dpg.does_item_exist(self._tag("queue_list")):
            dpg.set_item_user_data(
                self._tag("queue_list"), [row.label for row in self._orders]
            )

    def _on_select_order(self, sender=None, app_data=None, user_data=None) -> None:
        self._selected_key = str(user_data or "")

    def _refresh_widgets(self) -> None:
        self._refresh_queue()

        live = self._tag("live_list")
        if dpg.does_item_exist(live):
            items = [h.label for h in self._live] or ["(no live agents)"]
            dpg.configure_item(live, items=items)

    def _on_reject(self, sender=None, app_data=None, user_data=None) -> None:
        if not self._connect_redis():
            return
        client = self._redis
        assert client is not None
        row = next(
            (order for order in self._orders if _row_key(order) == self._selected_key),
            None,
        )
        if row is None or not row.order_id:
            return
        if row.status in TERMINAL_STATUSES:
            return
        agent_id = next(
            (live.agent_id for live in self._live if live.order_id == row.order_id),
            "",
        )
        try:
            client.xadd(
                wire.CLOUDFINISHED_STREAM,
                wire.cloudfinished_fields(
                    order_id=row.order_id,
                    status=wire.STATUS_CANCELLED,
                    agent_id=agent_id,
                ),
            )
        except RedisError:
            self._redis = None
            self._persistent = None
            return
        self._poll(force=True)


def build_ui(
    parent: str,
    *,
    tag_prefix: str,
    width: int = 420,
    height: int = 160,
) -> None:
    """Module-level builder for FeSpec / MegaDesk canvas hosting."""
    CloudFactoryFE().build_ui(
        parent, tag_prefix=tag_prefix, width=width, height=height
    )


def main() -> None:
    raise SystemExit(
        "CloudFactory FE is canvas-only. Drop it from the MegaDesk Catalog."
    )


if __name__ == "__main__":
    main()
