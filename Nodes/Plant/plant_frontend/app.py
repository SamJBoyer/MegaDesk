"""Plant Floor — canvas monitor for WORKORDER queue, live sandboxes, and Floor."""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from typing import Optional

import dearpygui.dearpygui as dpg
import redis
from megadesk_contracts import frame_pump
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError

from PlantManager.env import load_plant_env
from PlantManager.floor import default_floor
from redis_packets import (
    LIVEHARNESS_PREFIX,
    WORKORDER_STREAM,
    parse_harness,
    parse_workorder,
)

load_plant_env()

POLL_INTERVAL_SEC = 1.5
DEFAULT_REDIS_URL = "redis://localhost:6379/0"
WORKORDER_GROUP = "plant"
WORKORDER_RECENT = 12
HARNESS_SCAN_COUNT = 100

COLOR_OK = (80, 200, 80, 255)
COLOR_ERR = (220, 70, 70, 255)
COLOR_DIM = (140, 140, 140, 255)
COLOR_MUTED = (100, 100, 110, 255)

_LIVE: dict[str, "PlantFloor"] = {}


@dataclass
class WorkorderRow:
    entry_id: str
    repo: str
    ticket_name: str
    new_wt: bool
    model: str
    label: str


@dataclass
class HarnessRow:
    guid: str
    ticket_id: str
    status: str
    error: str
    label: str


@dataclass
class FloorRepo:
    name: str
    tickets: list[str]
    label: str


def _redis_url() -> str:
    return os.environ.get("REDIS_URL", DEFAULT_REDIS_URL)


class PlantFloor:
    """Read-only Plant pipeline monitor (does not consume WORKORDER)."""

    def __init__(self) -> None:
        self._redis: Optional[redis.Redis] = None
        self._root_tag = "primary"
        self._frame_registered = False
        self._last_poll = 0.0
        self._redis_ok = False
        self._docker_ok = False
        self._floor = default_floor()
        self._status = ""
        self._workorders: list[WorkorderRow] = []
        self._harnesses: list[HarnessRow] = []
        self._repos: list[FloorRepo] = []
        self._containers: list[str] = []
        self._pending = 0
        self._stream_len = 0
        self._log_lines: list[str] = []
        self._selected_harness: Optional[str] = None
        self._selected_repo: Optional[str] = None

    def _tag(self, suffix: str) -> str:
        return f"{self._root_tag}::{suffix}"

    def _append_log(self, text: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self._log_lines.append(f"[{stamp}] {text}")
        self._log_lines = self._log_lines[-60:]
        tag = self._tag("log")
        if dpg.does_item_exist(tag):
            dpg.set_value(tag, "\n".join(self._log_lines))

    def _connect_redis(self) -> Optional[redis.Redis]:
        if self._redis is not None:
            try:
                self._redis.ping()
                return self._redis
            except (RedisConnectionError, RedisTimeoutError, OSError, RedisError):
                self._redis = None
        try:
            client = redis.Redis.from_url(
                _redis_url(),
                decode_responses=True,
                socket_connect_timeout=1.5,
                socket_timeout=2.0,
            )
            client.ping()
            self._redis = client
            return client
        except (RedisConnectionError, RedisTimeoutError, OSError, RedisError):
            self._redis = None
            return None

    def _probe_docker(self) -> bool:
        try:
            proc = subprocess.run(
                ["docker", "info"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            return proc.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return False

    def _list_plant_containers(self) -> list[str]:
        try:
            proc = subprocess.run(
                [
                    "docker",
                    "ps",
                    "--filter",
                    "name=pm-",
                    "--format",
                    "{{.Names}}\t{{.Status}}",
                ],
                capture_output=True,
                text=True,
                timeout=4,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return []
        if proc.returncode != 0:
            return []
        lines: list[str] = []
        for raw in proc.stdout.splitlines():
            raw = raw.strip()
            if raw:
                lines.append(raw.replace("\t", "  ·  "))
        return lines

    def _scan_harnesses(self, client: redis.Redis) -> list[HarnessRow]:
        rows: list[HarnessRow] = []
        cursor: int | str = 0
        while True:
            cursor, batch = client.scan(
                cursor=cursor, match=f"{LIVEHARNESS_PREFIX}*", count=HARNESS_SCAN_COUNT
            )
            for key in batch:
                if client.type(key) != "hash":
                    continue
                guid = key[len(LIVEHARNESS_PREFIX) :]
                if not guid:
                    continue
                try:
                    parsed = parse_harness(client.hgetall(key))
                except ValueError:
                    continue
                status = parsed["status"] or "?"
                err = parsed["error"]
                short = guid if len(guid) <= 8 else guid[:8]
                label = f"{short}…  {status}  ticket={parsed['ticket_id']}"
                if err:
                    err_short = err if len(err) <= 40 else err[:37] + "…"
                    label = f"{label}  ! {err_short}"
                rows.append(
                    HarnessRow(
                        guid=guid,
                        ticket_id=parsed["ticket_id"],
                        status=status,
                        error=err,
                        label=label,
                    )
                )
            if cursor == 0 or cursor == "0":
                break
        rows.sort(key=lambda r: (r.status, r.guid))
        return rows

    def _pending_count(self, client: redis.Redis) -> int:
        try:
            info = client.xpending(WORKORDER_STREAM, WORKORDER_GROUP)
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

    def _recent_workorders(self, client: redis.Redis) -> list[WorkorderRow]:
        try:
            entries = client.xrevrange(WORKORDER_STREAM, count=WORKORDER_RECENT)
        except RedisError:
            return []
        rows: list[WorkorderRow] = []
        for entry_id, fields in entries:
            try:
                parsed = parse_workorder(fields)
            except ValueError:
                rows.append(
                    WorkorderRow(
                        entry_id=entry_id,
                        repo="?",
                        ticket_name="?",
                        new_wt=True,
                        model="",
                        label=f"{entry_id}  (unparseable)",
                    )
                )
                continue
            mode = "new" if parsed["new_wt"] else "reuse"
            short_id = entry_id if len(entry_id) <= 14 else entry_id[:14]
            label = (
                f"{short_id}  {parsed['repo']}/{parsed['ticket_name']}  "
                f"[{mode}]  {parsed['model']}"
            )
            rows.append(
                WorkorderRow(
                    entry_id=entry_id,
                    repo=parsed["repo"],
                    ticket_name=parsed["ticket_name"],
                    new_wt=bool(parsed["new_wt"]),
                    model=parsed["model"],
                    label=label,
                )
            )
        return rows

    def _scan_floor(self) -> list[FloorRepo]:
        floor = self._floor
        if not floor.is_dir():
            return []
        repos: list[FloorRepo] = []
        for child in sorted(floor.iterdir()):
            if not child.is_dir():
                continue
            if not (child / ".bare").is_dir():
                continue
            tickets_dir = child / "wt" / "tickets"
            tickets: list[str] = []
            if tickets_dir.is_dir():
                tickets = sorted(
                    p.name for p in tickets_dir.iterdir() if p.is_dir()
                )
            n = len(tickets)
            sample = ", ".join(tickets[:4])
            if n > 4:
                sample = f"{sample}, …"
            suffix = f"  tickets={n}" + (f" ({sample})" if sample else "")
            repos.append(
                FloorRepo(name=child.name, tickets=tickets, label=f"{child.name}{suffix}")
            )
        return repos

    def _poll(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and (now - self._last_poll) < POLL_INTERVAL_SEC:
            return
        self._last_poll = now

        client = self._connect_redis()
        self._redis_ok = client is not None
        self._docker_ok = self._probe_docker()
        self._floor = default_floor()
        self._repos = self._scan_floor()
        self._containers = self._list_plant_containers() if self._docker_ok else []

        if client is None:
            self._workorders = []
            self._harnesses = []
            self._pending = 0
            self._stream_len = 0
            self._status = "Redis unreachable"
            self._refresh_widgets()
            return

        try:
            self._stream_len = int(client.xlen(WORKORDER_STREAM))
            self._pending = self._pending_count(client)
            self._workorders = self._recent_workorders(client)
            self._harnesses = self._scan_harnesses(client)
            self._status = (
                f"queue={self._stream_len}  pending={self._pending}  "
                f"live={len(self._harnesses)}  floor={len(self._repos)}  "
                f"docker={len(self._containers)}"
            )
        except RedisError as exc:
            self._redis_ok = False
            self._redis = None
            self._status = f"Redis error: {exc}"

        self._refresh_widgets()

    def _dot(self, ok: bool) -> tuple[int, int, int, int]:
        return COLOR_OK if ok else COLOR_ERR

    def _refresh_widgets(self) -> None:
        if dpg.does_item_exist(self._tag("redis_dot")):
            dpg.configure_item(self._tag("redis_dot"), color=self._dot(self._redis_ok))
        if dpg.does_item_exist(self._tag("docker_dot")):
            dpg.configure_item(self._tag("docker_dot"), color=self._dot(self._docker_ok))
        if dpg.does_item_exist(self._tag("status_lbl")):
            dpg.set_value(self._tag("status_lbl"), self._status)
        if dpg.does_item_exist(self._tag("floor_path")):
            dpg.set_value(self._tag("floor_path"), str(self._floor))

        queue = self._tag("queue_list")
        if dpg.does_item_exist(queue):
            items = [w.label for w in self._workorders] or ["(no WORKORDER entries)"]
            dpg.configure_item(queue, items=items)

        live = self._tag("live_list")
        if dpg.does_item_exist(live):
            items = [h.label for h in self._harnesses] or ["(no live harnesses)"]
            dpg.configure_item(live, items=items)

        floor = self._tag("floor_list")
        if dpg.does_item_exist(floor):
            items = [r.label for r in self._repos] or ["(Floor empty)"]
            dpg.configure_item(floor, items=items)

        dock = self._tag("docker_list")
        if dpg.does_item_exist(dock):
            items = self._containers or ["(no pm-* containers)"]
            dpg.configure_item(dock, items=items)

        detail = self._tag("detail")
        if dpg.does_item_exist(detail):
            dpg.set_value(detail, self._detail_text())

    def _detail_text(self) -> str:
        if self._selected_harness:
            for h in self._harnesses:
                if h.guid == self._selected_harness:
                    err = h.error or "(none)"
                    return (
                        f"LIVEHARNESS:{h.guid}\n"
                        f"status: {h.status}\n"
                        f"ticket_id: {h.ticket_id}\n"
                        f"error: {err}"
                    )
        if self._selected_repo:
            for r in self._repos:
                if r.name == self._selected_repo:
                    tickets = "\n".join(f"  · {t}" for t in r.tickets) or "  (none)"
                    path = self._floor / r.name
                    return (
                        f"Floor/{r.name}\n"
                        f"path: {path}\n"
                        f"tickets ({len(r.tickets)}):\n{tickets}"
                    )
        if self._workorders:
            w = self._workorders[0]
            return (
                f"Latest WORKORDER {w.entry_id}\n"
                f"repo: {w.repo}\n"
                f"ticket: {w.ticket_name}\n"
                f"new_wt: {w.new_wt}\n"
                f"model: {w.model}"
            )
        return "Select a live harness or Floor repo for details."

    def build_ui(
        self,
        parent: str,
        *,
        tag_prefix: str,
        width: int = 720,
        height: int = 640,
    ) -> None:
        """Fill the host content parent with Plant Floor widgets."""
        self._root_tag = tag_prefix
        _ = width, height

        with dpg.group(parent=parent):
            dpg.add_text("Plant Floor")
            dpg.add_spacer(height=4)

            with dpg.group(horizontal=True):
                dpg.add_text("Redis:")
                dpg.add_text("*", tag=self._tag("redis_dot"), color=COLOR_ERR)
                dpg.add_spacer(width=12)
                dpg.add_text("Docker:")
                dpg.add_text("*", tag=self._tag("docker_dot"), color=COLOR_ERR)
                dpg.add_spacer(width=10)
                dpg.add_text("", tag=self._tag("status_lbl"), wrap=360, color=COLOR_DIM)

            with dpg.group(horizontal=True):
                dpg.add_text("Floor", color=COLOR_MUTED)
                dpg.add_text("", tag=self._tag("floor_path"), color=COLOR_MUTED, wrap=520)

            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Refresh",
                    width=80,
                    callback=lambda: self._on_refresh(),
                )
                dpg.add_button(
                    label="Open Floor",
                    width=90,
                    callback=lambda: self._on_open_floor(),
                )
                dpg.add_button(
                    label="Clear log",
                    width=80,
                    callback=lambda: self._on_clear_log(),
                )

            dpg.add_separator()

            with dpg.group(horizontal=True):
                with dpg.child_window(width=360, height=280, border=True):
                    dpg.add_text("WORKORDER (recent)", color=COLOR_DIM)
                    dpg.add_listbox(
                        items=["(loading…)"],
                        tag=self._tag("queue_list"),
                        num_items=10,
                        width=-1,
                        callback=self._on_queue_select,
                    )

                with dpg.child_window(width=-1, height=280, border=True):
                    dpg.add_text("LIVEHARNESS", color=COLOR_DIM)
                    dpg.add_listbox(
                        items=["(loading…)"],
                        tag=self._tag("live_list"),
                        num_items=10,
                        width=-1,
                        callback=self._on_live_select,
                    )

            with dpg.group(horizontal=True):
                with dpg.child_window(width=360, height=160, border=True):
                    dpg.add_text("Floor repos", color=COLOR_DIM)
                    dpg.add_listbox(
                        items=["(loading…)"],
                        tag=self._tag("floor_list"),
                        num_items=5,
                        width=-1,
                        callback=self._on_floor_select,
                    )

                with dpg.child_window(width=-1, height=160, border=True):
                    dpg.add_text("Docker sandboxes (pm-*)", color=COLOR_DIM)
                    dpg.add_listbox(
                        items=["(loading…)"],
                        tag=self._tag("docker_list"),
                        num_items=5,
                        width=-1,
                    )

            dpg.add_separator()
            dpg.add_text("Detail", color=COLOR_DIM)
            dpg.add_input_text(
                tag=self._tag("detail"),
                default_value="",
                multiline=True,
                readonly=True,
                width=-1,
                height=90,
            )

            dpg.add_text("Log", color=COLOR_DIM)
            dpg.add_input_text(
                tag=self._tag("log"),
                default_value="",
                multiline=True,
                readonly=True,
                width=-1,
                height=-1,
            )

        dpg.set_item_user_data(parent, self.shutdown)
        self._append_log(f"Monitoring floor={self._floor}")
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

    def _on_refresh(self) -> None:
        self._poll(force=True)
        self._append_log("Refreshed")

    def _on_clear_log(self) -> None:
        self._log_lines.clear()
        tag = self._tag("log")
        if dpg.does_item_exist(tag):
            dpg.set_value(tag, "")

    def _on_open_floor(self) -> None:
        path = self._floor
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._append_log(f"Open Floor failed: {exc}")
            return
        try:
            if os.name == "nt":
                os.startfile(str(path))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(
                    ["xdg-open", str(path)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            self._append_log(f"Opened {path}")
        except OSError as exc:
            self._append_log(f"Open Floor failed: {exc}")

    def _on_queue_select(self, _sender, app_data, _user_data=None) -> None:
        label = str(app_data if app_data is not None else "").strip()
        self._selected_harness = None
        self._selected_repo = None
        for w in self._workorders:
            if w.label == label:
                detail = self._tag("detail")
                if dpg.does_item_exist(detail):
                    dpg.set_value(
                        detail,
                        f"WORKORDER {w.entry_id}\n"
                        f"repo: {w.repo}\n"
                        f"ticket: {w.ticket_name}\n"
                        f"new_wt: {w.new_wt}\n"
                        f"model: {w.model}",
                    )
                return

    def _on_live_select(self, _sender, app_data, _user_data=None) -> None:
        label = str(app_data if app_data is not None else "").strip()
        self._selected_repo = None
        for h in self._harnesses:
            if h.label == label:
                self._selected_harness = h.guid
                self._refresh_widgets()
                return
        self._selected_harness = None

    def _on_floor_select(self, _sender, app_data, _user_data=None) -> None:
        label = str(app_data if app_data is not None else "").strip()
        self._selected_harness = None
        for r in self._repos:
            if r.label == label:
                self._selected_repo = r.name
                self._refresh_widgets()
                return
        self._selected_repo = None


def build_ui(
    parent: str,
    *,
    tag_prefix: str,
    width: int = 720,
    height: int = 640,
) -> None:
    """Module-level builder for FeSpec / MegaDesk canvas hosting."""
    PlantFloor().build_ui(
        parent,
        tag_prefix=tag_prefix,
        width=width,
        height=height,
    )


def main() -> None:
    raise SystemExit(
        "Plant FE is canvas-only. Drop it from the MegaDesk Catalog."
    )


if __name__ == "__main__":
    main()
