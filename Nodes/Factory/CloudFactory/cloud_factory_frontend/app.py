"""CloudFactory canvas monitor — queued CLOUDORDERs and live agents."""

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
from megadesk_contracts.wire.factory import is_terminal
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError

POLL_INTERVAL_SEC = 1.5
CLOUDORDER_RECENT = 12
FINISHED_RECENT = 12
HASH_SCAN_COUNT = 100

COLOR_OK = (80, 200, 80, 255)
COLOR_ERR = (220, 70, 70, 255)
COLOR_DIM = (140, 140, 140, 255)

_LIVE: dict[str, "CloudFactoryFE"] = {}


@dataclass
class OrderRow:
    entry_id: str
    order_id: str
    title: str
    model: str
    status: str
    label: str


@dataclass
class LiveRow:
    agent_id: str
    title: str
    status: str
    label: str


class CloudFactoryFE:
    """Read-only CloudFactory monitor: queue, live agents, error lamp."""

    def __init__(self) -> None:
        self.redis_url = resolve_redis_url()
        self._root_tag = "primary"
        self._frame_registered = False
        self._last_poll = 0.0
        self._has_error = False
        self._redis: Optional[redis.Redis] = None
        self._persistent: Optional[redis.Redis] = None
        self._orders: list[OrderRow] = []
        self._live: list[LiveRow] = []

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
        height: int = 140,
    ) -> None:
        self._root_tag = tag_prefix
        _ = width, height
        col_w = 190

        with dpg.group(parent=parent):
            with dpg.group(horizontal=True):
                with dpg.group():
                    dpg.add_text("Queue", color=COLOR_DIM)
                    dpg.add_listbox(
                        items=["(loading…)"],
                        tag=self._tag("queue_list"),
                        num_items=2,
                        width=col_w,
                    )
                with dpg.group():
                    dpg.add_text("Live", color=COLOR_DIM)
                    dpg.add_listbox(
                        items=["(loading…)"],
                        tag=self._tag("live_list"),
                        num_items=2,
                        width=-1,
                    )
                with dpg.drawlist(width=16, height=16):
                    dpg.draw_circle(
                        (8, 8),
                        6,
                        fill=COLOR_ERR,
                        color=COLOR_ERR,
                        tag=self._tag("error_lamp"),
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

    def shutdown(self) -> None:
        if self._frame_registered:
            frame_pump.unregister(self._on_frame)
            self._frame_registered = False
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
            self._has_error = True
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
            self._has_error = any(
                (done.get("status") in {wire.STATUS_ERROR, wire.STATUS_STARTUP_ERROR})
                for done in finished.values()
            ) or any(
                run.get("status") in {wire.STATUS_ERROR, wire.STATUS_STARTUP_ERROR}
                for run in runs.values()
            )
        except RedisError:
            self._redis = None
            self._persistent = None
            self._has_error = True

        self._refresh_widgets()

    def _refresh_widgets(self) -> None:
        lamp = self._tag("error_lamp")
        if dpg.does_item_exist(lamp):
            color = COLOR_ERR if self._has_error else COLOR_OK
            dpg.configure_item(lamp, fill=color, color=color)
            dpg.set_item_user_data(lamp, self._has_error)

        queue = self._tag("queue_list")
        if dpg.does_item_exist(queue):
            items = [w.label for w in self._orders] or ["(no CLOUDORDER entries)"]
            dpg.configure_item(queue, items=items)

        live = self._tag("live_list")
        if dpg.does_item_exist(live):
            items = [h.label for h in self._live] or ["(no live agents)"]
            dpg.configure_item(live, items=items)


def build_ui(
    parent: str,
    *,
    tag_prefix: str,
    width: int = 420,
    height: int = 140,
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
