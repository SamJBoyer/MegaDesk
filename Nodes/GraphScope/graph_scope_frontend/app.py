"""GraphScope — canvas monitor for AgentHandler work-graph runs.

Read-only. Never consumes GRAPHEVENT; SCAN / XREVRANGE only.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import dearpygui.dearpygui as dpg
import redis
from megadesk_contracts import frame_pump, redis_connect, resolve_ephemeral_db, resolve_redis_url
from megadesk_contracts.wire import graph as wire
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError

POLL_INTERVAL_SEC = 1.5
RUN_SCAN_COUNT = 100
EVENT_SCAN_COUNT = 200

COLOR_OK = (80, 200, 80, 255)
COLOR_ERR = (220, 70, 70, 255)
COLOR_DIM = (140, 140, 140, 255)
COLOR_MUTED = (100, 100, 110, 255)
COLOR_RUN = (220, 180, 50, 255)
COLOR_BOX = (40, 42, 48, 255)
COLOR_EDGE = (90, 94, 104, 255)
COLOR_TEXT = (210, 210, 214, 255)

_STATUS_FILL = {
    wire.STATUS_QUEUED: (70, 74, 84, 255),
    wire.STATUS_RUNNING: COLOR_RUN,
    wire.STATUS_FINISHED: COLOR_OK,
    wire.STATUS_ERROR: COLOR_ERR,
    wire.STATUS_CANCELLED: COLOR_MUTED,
}

_LIVE: dict[str, "GraphScope"] = {}

DRAW_W = 460
DRAW_H = 72
BOX_W = 78
BOX_H = 34


@dataclass
class GraphRunRow:
    guid: str
    status: str
    current: str
    ticket_name: str
    repo: str
    spec: wire.GraphSpec
    nodes: dict[str, dict[str, str]]
    error: str
    label: str


def _redis_url() -> str:
    return resolve_redis_url()


class GraphScope:
    """Read-only work-graph monitor."""

    def __init__(self) -> None:
        self._redis: Optional[redis.Redis] = None
        self._root_tag = "primary"
        self._frame_registered = False
        self._last_poll = 0.0
        self._redis_ok = False
        self._status = ""
        self._runs: list[GraphRunRow] = []
        self._selected: Optional[str] = None
        self._events: list[dict[str, str]] = []

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
            client = redis_connect(
                _redis_url(),
                db=resolve_ephemeral_db(_redis_url()),
                socket_connect_timeout=1.5,
                socket_timeout=2.0,
            )
            client.ping()
            self._redis = client
            return client
        except (RedisConnectionError, RedisTimeoutError, OSError, RedisError):
            self._redis = None
            return None

    def _scan_runs(self, client: redis.Redis) -> list[GraphRunRow]:
        rows: list[GraphRunRow] = []
        cursor: int | str = 0
        while True:
            cursor, batch = client.scan(
                cursor=cursor,
                match=f"{wire.GRAPHRUN_PREFIX}*",
                count=RUN_SCAN_COUNT,
            )
            for key in batch:
                if client.type(key) != "hash":
                    continue
                try:
                    parsed = wire.parse_graph_run(client.hgetall(key))
                    spec = wire.decode_spec(parsed["spec"]) if parsed["spec"] else wire.WORK_GRAPH
                    nodes = wire.decode_nodes(parsed["nodes"])
                except ValueError:
                    continue
                guid = parsed["guid"] or wire.guid_from_graph_run_key(str(key))
                short = guid if len(guid) <= 8 else guid[:8]
                current = parsed["current"] or parsed["status"]
                ticket = parsed["ticket_name"]
                label = f"{short}  {parsed['status']}"
                if ticket:
                    label = f"{label}  {ticket}"
                if current and current != parsed["status"]:
                    label = f"{label}  {current}"
                rows.append(
                    GraphRunRow(
                        guid=guid,
                        status=parsed["status"],
                        current=parsed["current"],
                        ticket_name=ticket,
                        repo=parsed["repo"],
                        spec=spec,
                        nodes=nodes,
                        error=parsed["error"],
                        label=label,
                    )
                )
            if cursor == 0 or cursor == "0":
                break
        rows.sort(key=lambda r: (r.status, r.guid))
        return rows

    def _poll(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and (now - self._last_poll) < POLL_INTERVAL_SEC:
            return
        self._last_poll = now

        client = self._connect_redis()
        self._redis_ok = client is not None
        if client is None:
            self._runs = []
            self._events = []
            self._status = "Redis unreachable"
            self._refresh_widgets()
            return

        try:
            self._runs = self._scan_runs(client)
            if self._selected and not any(r.guid == self._selected for r in self._runs):
                self._selected = None
            if self._selected is None and self._runs:
                self._selected = self._runs[0].guid
            self._events = (
                wire.read_graph_events(
                    client, self._selected, count=EVENT_SCAN_COUNT
                )
                if self._selected
                else []
            )
            live = sum(1 for r in self._runs if r.status == wire.STATUS_RUNNING)
            self._status = f"runs={len(self._runs)}  live={live}"
        except RedisError as exc:
            self._redis_ok = False
            self._redis = None
            self._status = f"Redis error: {exc}"

        self._refresh_widgets()

    def _selected_row(self) -> Optional[GraphRunRow]:
        if not self._selected:
            return None
        for row in self._runs:
            if row.guid == self._selected:
                return row
        return None

    def _refresh_widgets(self) -> None:
        if dpg.does_item_exist(self._tag("redis_dot")):
            dpg.configure_item(
                self._tag("redis_dot"),
                color=COLOR_OK if self._redis_ok else COLOR_ERR,
            )
        if dpg.does_item_exist(self._tag("status_lbl")):
            dpg.set_value(self._tag("status_lbl"), self._status)

        run_list = self._tag("run_list")
        if dpg.does_item_exist(run_list):
            items = [r.label for r in self._runs] or ["(none)"]
            dpg.configure_item(run_list, items=items)
            if self._selected:
                for row in self._runs:
                    if row.guid == self._selected:
                        dpg.set_value(run_list, row.label)
                        break

        row = self._selected_row()
        spec = row.spec if row is not None else wire.WORK_GRAPH
        progress = row.nodes if row is not None else wire.initial_nodes(spec)
        self._draw_graph(spec, progress, row.current if row else "")
        if dpg.does_item_exist(self._tag("graph_nodes")):
            dpg.set_value(self._tag("graph_nodes"), " → ".join(spec.node_names()))

        detail = self._tag("detail")
        if dpg.does_item_exist(detail):
            dpg.set_value(detail, self._detail_text(row))

    def _detail_text(self, row: Optional[GraphRunRow]) -> str:
        if row is None:
            return ""
        lines = [f"{row.guid}  {row.status}"]
        if row.ticket_name or row.repo:
            lines.append("  ".join(p for p in (row.repo, row.ticket_name) if p))
        if row.error:
            lines.append(row.error)
        for event in self._events[-8:]:
            bit = f"{event['node']} {event['status']}"
            if event["detail"]:
                bit = f"{bit}  {event['detail'][:80]}"
            lines.append(bit)
        return "\n".join(lines)

    def _draw_graph(
        self,
        spec: wire.GraphSpec,
        progress: dict[str, dict[str, str]],
        current: str,
    ) -> None:
        tag = self._tag("graph_dl")
        if not dpg.does_item_exist(tag):
            return
        dpg.delete_item(tag, children_only=True)
        names = spec.node_names()
        if not names:
            return
        gap = 16
        total = len(names) * BOX_W + max(0, len(names) - 1) * gap
        x0 = max(8, (DRAW_W - total) / 2)
        y = (DRAW_H - BOX_H) / 2
        centers: list[tuple[float, float]] = []
        for i, name in enumerate(names):
            x = x0 + i * (BOX_W + gap)
            centers.append((x + BOX_W / 2, y + BOX_H / 2))
            status = (progress.get(name) or {}).get("status") or wire.STATUS_QUEUED
            fill = _STATUS_FILL.get(status, COLOR_BOX)
            if name == current:
                fill = COLOR_RUN
            node = spec.node(name)
            dpg.draw_rectangle(
                (x, y),
                (x + BOX_W, y + BOX_H),
                parent=tag,
                fill=fill,
                color=COLOR_EDGE,
                thickness=1,
                tag=self._tag(f"box_{name}"),
            )
            dpg.draw_text(
                (x + 6, y + 10),
                node.label,
                parent=tag,
                size=12,
                color=COLOR_TEXT,
            )
        name_at = {node.name: i for i, node in enumerate(spec.nodes)}
        for source, target in spec.edges:
            if source not in name_at or target not in name_at:
                continue
            a = centers[name_at[source]]
            b = centers[name_at[target]]
            dpg.draw_line(
                (a[0] + BOX_W / 2 - 2, a[1]),
                (b[0] - BOX_W / 2 + 2, b[1]),
                parent=tag,
                color=COLOR_EDGE,
                thickness=1,
            )

    def _on_run_select(self, _sender, app_data, _user_data=None) -> None:
        label = str(app_data if app_data is not None else "").strip()
        for row in self._runs:
            if row.label == label:
                self._selected = row.guid
                self._poll(force=True)
                return

    def build_ui(
        self,
        parent: str,
        *,
        tag_prefix: str,
        width: int = 480,
        height: int = 240,
    ) -> None:
        self._root_tag = tag_prefix
        _ = width, height

        with dpg.group(parent=parent):
            with dpg.group(horizontal=True):
                dpg.add_text("R")
                dpg.add_text("*", tag=self._tag("redis_dot"), color=COLOR_ERR)
                dpg.add_spacer(width=8)
                dpg.add_text("", tag=self._tag("status_lbl"), wrap=360, color=COLOR_DIM)

            dpg.add_listbox(
                items=["(none)"],
                tag=self._tag("run_list"),
                num_items=2,
                width=-1,
                callback=self._on_run_select,
            )
            dpg.add_text("", tag=self._tag("graph_nodes"), color=COLOR_MUTED)
            with dpg.drawlist(
                width=DRAW_W,
                height=DRAW_H,
                tag=self._tag("graph_dl"),
            ):
                pass
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
        _LIVE.pop(self._root_tag, None)


def build_ui(
    parent: str,
    *,
    tag_prefix: str,
    width: int = 480,
    height: int = 240,
) -> None:
    GraphScope().build_ui(
        parent,
        tag_prefix=tag_prefix,
        width=width,
        height=height,
    )
