"""CloudFactory canvas monitor — processed orders, live agents, drafts to approve.

MachineFactory is the template: Redis light, queue, live list, detail. Drafts
are the cloud extra — TicketDispatcher supplies the GitHub URL and issue text,
and a spoken sentence still must not open a PR on its own.
"""

from __future__ import annotations

import time
import webbrowser
from dataclasses import dataclass
from typing import Optional

import dearpygui.dearpygui as dpg
import redis
from megadesk_contracts import REDIS_DB_PERSISTENT, frame_pump, resolve_redis_url
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
COLOR_BLUE = (70, 140, 230, 255)
COLOR_AMBER = (215, 170, 70, 255)
COLOR_DIM = (140, 140, 140, 255)

# Patchable so a test can assert a link was opened without a browser appearing.
open_url = webbrowser.open

_LIVE: dict[str, "CloudFactoryFE"] = {}


@dataclass
class OrderRow:
    entry_id: str
    order_id: str
    title: str
    repo_url: str
    model: str
    status: str
    pr_url: str
    label: str


@dataclass
class LiveRow:
    agent_id: str
    order_id: str
    title: str
    status: str
    pr_url: str
    label: str


@dataclass
class DraftRow:
    order_id: str
    title: str
    repo_url: str
    instructions: str
    model: str
    label: str


class CloudFactoryFE:
    """Read-only CloudFactory monitor plus draft approval."""

    def __init__(self) -> None:
        self.redis_url = resolve_redis_url()
        self._root_tag = "primary"
        self._frame_registered = False
        self._last_poll = 0.0
        self._redis_ok = False
        self._status = ""
        self._status_color = COLOR_DIM
        self._redis: Optional[redis.Redis] = None
        self._persistent: Optional[redis.Redis] = None
        self._orders: list[OrderRow] = []
        self._live: list[LiveRow] = []
        self._drafts: list[DraftRow] = []
        self._runs_by_order: dict[str, dict[str, str]] = {}
        self._pending = 0
        self._stream_len = 0
        self._selected_order: Optional[str] = None
        self._selected_agent: Optional[str] = None
        self._selected_draft: Optional[str] = None

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
            client = redis.Redis.from_url(
                self.redis_url,
                decode_responses=True,
                socket_connect_timeout=1.5,
                socket_timeout=2.0,
            )
            client.ping()
            persistent = redis.Redis.from_url(
                self.redis_url,
                db=REDIS_DB_PERSISTENT,
                decode_responses=True,
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
        width: int = 520,
        height: int = 320,
    ) -> None:
        self._root_tag = tag_prefix
        _ = width, height
        col_w = 250

        with dpg.group(parent=parent):
            with dpg.group(horizontal=True):
                dpg.add_text("R")
                dpg.add_text("*", tag=self._tag("redis_dot"), color=COLOR_ERR)
                dpg.add_spacer(width=8)
                dpg.add_text(
                    "", tag=self._tag("status_lbl"), wrap=280, color=COLOR_DIM
                )
                dpg.add_spacer(width=6)
                dpg.add_button(
                    label="Refresh",
                    width=58,
                    callback=lambda: self._on_refresh(),
                )

            with dpg.group(horizontal=True):
                with dpg.group():
                    with dpg.group(horizontal=True):
                        dpg.add_text("Queue", color=COLOR_DIM)
                        dpg.add_button(
                            label="PR",
                            width=28,
                            height=20,
                            tag=self._tag("pr_btn"),
                            show=False,
                            callback=self._on_open_pr,
                        )
                    dpg.add_listbox(
                        items=["(loading…)"],
                        tag=self._tag("queue_list"),
                        num_items=2,
                        width=col_w,
                        callback=self._on_queue_select,
                    )
                with dpg.group():
                    dpg.add_text("Live", color=COLOR_DIM)
                    dpg.add_listbox(
                        items=["(loading…)"],
                        tag=self._tag("live_list"),
                        num_items=2,
                        width=-1,
                        callback=self._on_live_select,
                    )

            with dpg.group():
                with dpg.group(horizontal=True):
                    dpg.add_text("Drafts", color=COLOR_DIM)
                    dpg.add_button(
                        label="go",
                        width=28,
                        height=20,
                        tag=self._tag("draft_go"),
                        callback=self._on_dispatch_draft,
                    )
                    dpg.add_button(
                        label="x",
                        width=20,
                        height=20,
                        tag=self._tag("draft_del"),
                        callback=self._on_discard_draft,
                    )
                dpg.add_listbox(
                    items=["(loading…)"],
                    tag=self._tag("draft_list"),
                    num_items=2,
                    width=-1,
                    callback=self._on_draft_select,
                )

            dpg.add_input_text(
                tag=self._tag("detail"),
                default_value="",
                multiline=True,
                readonly=True,
                width=-1,
                height=52,
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
        # Drafts and runs are deliberately left on db 1: a closed panel is not a
        # cancelled agent, and the PR link has to survive until someone reads it.
        _LIVE.pop(self._root_tag, None)

    def _on_refresh(self) -> None:
        self._poll(force=True)

    def _on_dispatch_draft(self, sender=None, app_data=None, user_data=None) -> None:
        order_id = self._selected_draft or ""
        if self._persistent is None or not order_id:
            return
        try:
            stored = self._persistent.hgetall(wire.clouddraft_key(order_id))
        except RedisError as exc:
            self._apply_status(f"Redis read failed: {exc}", COLOR_ERR)
            return
        if not stored:
            self._apply_status("That draft is gone", COLOR_AMBER)
            self._selected_draft = None
            self._poll(force=True)
            return

        try:
            # Parsed and rebuilt rather than forwarded: a draft has sat on db 1
            # since some earlier version wrote it, and CLOUDORDER's field set is
            # the one thing the BE is entitled to assume.
            order = wire.cloudorder_fields(**wire.parse_cloudorder(stored))
        except ValueError as exc:
            self._apply_status(f"Unusable draft: {exc}", COLOR_ERR)
            return
        if not self._publish(order):
            return

        # Deleted here, not on launch: the row has to stop offering a button the
        # moment it is pressed, or an impatient second click means two PRs.
        try:
            self._persistent.delete(wire.clouddraft_key(order_id))
        except RedisError:
            pass
        self._selected_draft = None
        self._apply_status(f"Dispatched — {order['title']}", COLOR_BLUE)
        self._poll(force=True)

    def _on_discard_draft(self, sender=None, app_data=None, user_data=None) -> None:
        order_id = self._selected_draft or ""
        if self._persistent is None or not order_id:
            return
        try:
            self._persistent.delete(wire.clouddraft_key(order_id))
        except RedisError:
            pass
        self._selected_draft = None
        self._apply_status("Draft discarded", COLOR_DIM)
        self._poll(force=True)

    def _on_open_pr(self, sender=None, app_data=None, user_data=None) -> None:
        url = self._selected_pr_url()
        if url:
            open_url(url)

    def _publish(self, order: dict[str, str]) -> bool:
        if not self._connect_redis() or self._redis is None:
            self._apply_status("Redis unavailable", COLOR_ERR)
            return False
        try:
            self._redis.xadd(wire.CLOUDORDER_STREAM, order)
        except RedisError as exc:
            self._apply_status(f"Redis xadd failed: {exc}", COLOR_ERR)
            self._redis = None
            return False
        return True

    def _pending_count(self, client: redis.Redis) -> int:
        try:
            info = client.xpending(wire.CLOUDORDER_STREAM, wire.CLOUDORDER_GROUP)
        except RedisError:
            return 0
        if isinstance(info, dict):
            return int(info.get("pending") or 0)
        if isinstance(info, (list, tuple)) and info:
            try:
                return int(info[0])
            except (TypeError, ValueError):
                return 0
        return 0

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

    def _scan_drafts(self, persistent: redis.Redis) -> list[DraftRow]:
        rows: list[DraftRow] = []
        try:
            keys = list(
                persistent.scan_iter(
                    match=f"{wire.CLOUDDRAFT_PREFIX}*", count=HASH_SCAN_COUNT
                )
            )
        except RedisError:
            return []
        for key in keys:
            try:
                order_id = wire.order_id_from_draft_key(key)
                draft = wire.parse_cloudorder(persistent.hgetall(key))
            except ValueError:
                continue
            short = order_id if len(order_id) <= 8 else order_id[:8]
            rows.append(
                DraftRow(
                    order_id=order_id,
                    title=draft["title"],
                    repo_url=draft["repo_url"],
                    instructions=draft["instructions"],
                    model=draft["model"],
                    label=f"{short}…  {draft['title']}",
                )
            )
        rows.sort(key=lambda r: r.title)
        return rows

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
                        repo_url="",
                        model="",
                        status="",
                        pr_url="",
                        label=f"{entry_id}  (unparseable)",
                    )
                )
                continue
            order_id = parsed["order_id"]
            run = runs_by_order.get(order_id) or {}
            done = finished_by_order.get(order_id) or {}
            status = str(run.get("status") or done.get("status") or "")
            pr_url = str(run.get("pr_url") or done.get("pr_url") or "")
            short_id = entry_id if len(entry_id) <= 14 else entry_id[:14]
            label = f"{short_id}  {parsed['title']}  {parsed['model']}"
            if status:
                label = f"{label}  {status}"
            rows.append(
                OrderRow(
                    entry_id=entry_id,
                    order_id=order_id,
                    title=parsed["title"],
                    repo_url=parsed["repo_url"],
                    model=parsed["model"],
                    status=status,
                    pr_url=pr_url,
                    label=label,
                )
            )
        return rows

    def _live_from_runs(self, runs: dict[str, dict[str, str]]) -> list[LiveRow]:
        rows: list[LiveRow] = []
        for agent_id, run in runs.items():
            status = run["status"]
            if is_terminal(status):
                continue
            short = agent_id if len(agent_id) <= 8 else agent_id[:8]
            rows.append(
                LiveRow(
                    agent_id=agent_id,
                    order_id=run["order_id"],
                    title=run["title"],
                    status=status,
                    pr_url=run["pr_url"],
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
            self._drafts = []
            self._runs_by_order = {}
            self._pending = 0
            self._stream_len = 0
            self._redis_ok = False
            self._status = "Redis unreachable"
            self._status_color = COLOR_ERR
            self._refresh_widgets()
            return

        client = self._redis
        persistent = self._persistent
        assert client is not None and persistent is not None
        self._redis_ok = True

        try:
            runs = self._scan_runs(persistent)
            finished = self._finished_by_order(client)
            runs_by_order = {run["order_id"]: run for run in runs.values()}
            self._runs_by_order = runs_by_order
            self._stream_len = int(client.xlen(wire.CLOUDORDER_STREAM))
            self._pending = self._pending_count(client)
            self._orders = self._recent_orders(client, runs_by_order, finished)
            self._live = self._live_from_runs(runs)
            self._drafts = self._scan_drafts(persistent)
            self._status = (
                f"queue={self._stream_len}  pending={self._pending}  "
                f"live={len(self._live)}  drafts={len(self._drafts)}"
            )
            self._status_color = COLOR_DIM
            latest = next(iter(finished.values()), None) if finished else None
            if latest and latest["status"] == wire.STATUS_STARTUP_ERROR:
                self._status = "Agent never started — check CURSOR_API_KEY"
                self._status_color = COLOR_ERR
            elif latest and latest["status"] == wire.STATUS_FINISHED and latest["pr_url"]:
                self._status = f"PR ready — {latest['pr_url']}"
                self._status_color = COLOR_OK
        except RedisError as exc:
            self._redis_ok = False
            self._redis = None
            self._persistent = None
            self._status = f"Redis error: {exc}"
            self._status_color = COLOR_ERR

        self._refresh_widgets()

    def _dot(self, ok: bool) -> tuple[int, int, int, int]:
        return COLOR_OK if ok else COLOR_ERR

    def _refresh_widgets(self) -> None:
        if dpg.does_item_exist(self._tag("redis_dot")):
            dpg.configure_item(self._tag("redis_dot"), color=self._dot(self._redis_ok))
        if dpg.does_item_exist(self._tag("status_lbl")):
            dpg.set_value(self._tag("status_lbl"), self._status)
            dpg.configure_item(self._tag("status_lbl"), color=self._status_color)

        queue = self._tag("queue_list")
        if dpg.does_item_exist(queue):
            items = [w.label for w in self._orders] or ["(no CLOUDORDER entries)"]
            dpg.configure_item(queue, items=items)

        live = self._tag("live_list")
        if dpg.does_item_exist(live):
            items = [h.label for h in self._live] or ["(no live agents)"]
            dpg.configure_item(live, items=items)

        drafts = self._tag("draft_list")
        if dpg.does_item_exist(drafts):
            items = [d.label for d in self._drafts] or ["(no drafts)"]
            dpg.configure_item(drafts, items=items)

        detail = self._tag("detail")
        if dpg.does_item_exist(detail):
            dpg.set_value(detail, self._detail_text())

        pr = self._tag("pr_btn")
        if dpg.does_item_exist(pr):
            url = self._selected_pr_url()
            dpg.configure_item(pr, show=bool(url), user_data=url)

    def _selected_pr_url(self) -> str:
        if self._selected_agent:
            for row in self._live:
                if row.agent_id == self._selected_agent and row.pr_url:
                    return row.pr_url
        if self._selected_order:
            for row in self._orders:
                if row.order_id == self._selected_order and row.pr_url:
                    return row.pr_url
        return ""

    def _detail_text(self) -> str:
        if self._selected_draft:
            for d in self._drafts:
                if d.order_id == self._selected_draft:
                    return (
                        f"CLOUDDRAFT:{d.order_id}\n"
                        f"title: {d.title}\n"
                        f"repo: {d.repo_url}\n"
                        f"model: {d.model}"
                    )
        if self._selected_agent:
            for h in self._live:
                if h.agent_id == self._selected_agent:
                    return (
                        f"CLOUDRUN:{h.agent_id}\n"
                        f"status: {h.status}\n"
                        f"title: {h.title}\n"
                        f"pr: {h.pr_url or '(none)'}"
                    )
        if self._selected_order:
            for w in self._orders:
                if w.order_id == self._selected_order:
                    return (
                        f"CLOUDORDER {w.entry_id}\n"
                        f"title: {w.title}\n"
                        f"status: {w.status or '(pending)'}\n"
                        f"pr: {w.pr_url or '(none)'}"
                    )
        if self._orders:
            w = self._orders[0]
            return (
                f"Latest CLOUDORDER {w.entry_id}\n"
                f"title: {w.title}\n"
                f"status: {w.status or '(pending)'}\n"
                f"model: {w.model}"
            )
        return ""

    def _apply_status(self, text: str, color: tuple[int, int, int, int]) -> None:
        self._status = text
        self._status_color = color
        tag = self._tag("status_lbl")
        if dpg.does_item_exist(tag):
            dpg.set_value(tag, text)
            dpg.configure_item(tag, color=color)

    def _on_queue_select(self, _sender, app_data, _user_data=None) -> None:
        label = str(app_data if app_data is not None else "").strip()
        self._selected_agent = None
        self._selected_draft = None
        for w in self._orders:
            if w.label == label:
                self._selected_order = w.order_id
                self._refresh_widgets()
                return
        self._selected_order = None
        self._refresh_widgets()

    def _on_live_select(self, _sender, app_data, _user_data=None) -> None:
        label = str(app_data if app_data is not None else "").strip()
        self._selected_order = None
        self._selected_draft = None
        for h in self._live:
            if h.label == label:
                self._selected_agent = h.agent_id
                self._refresh_widgets()
                return
        self._selected_agent = None
        self._refresh_widgets()

    def _on_draft_select(self, _sender, app_data, _user_data=None) -> None:
        label = str(app_data if app_data is not None else "").strip()
        self._selected_order = None
        self._selected_agent = None
        for d in self._drafts:
            if d.label == label:
                self._selected_draft = d.order_id
                self._refresh_widgets()
                return
        self._selected_draft = None
        self._refresh_widgets()


def build_ui(
    parent: str,
    *,
    tag_prefix: str,
    width: int = 520,
    height: int = 320,
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
