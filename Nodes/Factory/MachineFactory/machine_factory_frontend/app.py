"""MachineFactory canvas monitor — queued orders, live agents, sandboxes."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from typing import Optional

from megadesk_contracts import host as dpg
import redis
from megadesk_contracts import frame_pump, redis_connect, resolve_ephemeral_db, resolve_redis_url
from megadesk_contracts.wire import machine as wire
from megadesk_contracts.wire.factory import STATUS_ERROR, STATUS_STARTUP_ERROR
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError

from MachineFactoryManager.pool import CONTAINER_NAME_PREFIX

POLL_INTERVAL_SEC = 1.5
WORKORDER_RECENT = 12
AGENT_HANDLER_SCAN_COUNT = 100

COLOR_OK = (80, 200, 80, 255)
COLOR_ERR = (220, 70, 70, 255)
COLOR_DIM = (140, 140, 140, 255)

_LIVE: dict[str, "MachineFactoryFE"] = {}


@dataclass
class WorkorderRow:
    entry_id: str
    repo: str
    ticket_name: str
    model: str
    auto_pr: bool
    label: str


@dataclass
class AgentHandlerRow:
    guid: str
    ticket_id: str
    status: str
    label: str


def _redis_url() -> str:
    return resolve_redis_url()


class MachineFactoryFE:
    """Read-only MachineFactory monitor (never consumes from the order stream)."""

    def __init__(self) -> None:
        self._redis: Optional[redis.Redis] = None
        self._root_tag = "primary"
        self._frame_registered = False
        self._last_poll = 0.0
        self._has_error = False
        self._workorders: list[WorkorderRow] = []
        self._agent_handlers: list[AgentHandlerRow] = []
        self._containers: list[str] = []

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

    def _list_sandbox_containers(self) -> list[str]:
        try:
            proc = subprocess.run(
                [
                    "docker",
                    "ps",
                    "--filter",
                    f"name={CONTAINER_NAME_PREFIX}",
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

    def _scan_agent_handlers(self, client: redis.Redis) -> list[AgentHandlerRow]:
        rows: list[AgentHandlerRow] = []
        cursor: int | str = 0
        while True:
            cursor, batch = client.scan(
                cursor=cursor,
                match=f"{wire.AGENTHANDLER_PREFIX}*",
                count=AGENT_HANDLER_SCAN_COUNT,
            )
            for key in batch:
                if client.type(key) != "hash":
                    continue
                guid = key[len(wire.AGENTHANDLER_PREFIX) :]
                if not guid:
                    continue
                try:
                    parsed = wire.parse_agent_handler(client.hgetall(key))
                except ValueError:
                    continue
                status = parsed["status"] or "?"
                short = guid if len(guid) <= 8 else guid[:8]
                label = f"{short}…  {status}  ticket={parsed['ticket_id']}"
                rows.append(
                    AgentHandlerRow(
                        guid=guid,
                        ticket_id=parsed["ticket_id"],
                        status=status,
                        label=label,
                    )
                )
            if cursor == 0 or cursor == "0":
                break
        rows.sort(key=lambda r: (r.status, r.guid))
        return rows

    def _recent_workorders(self, client: redis.Redis) -> list[WorkorderRow]:
        try:
            entries = client.xrevrange(wire.WORKORDER_STREAM, count=WORKORDER_RECENT)
        except RedisError:
            return []
        rows: list[WorkorderRow] = []
        for entry_id, fields in entries:
            try:
                parsed = wire.parse_workorder(fields)
            except ValueError:
                rows.append(
                    WorkorderRow(
                        entry_id=entry_id,
                        repo="?",
                        ticket_name="?",
                        model="",
                        auto_pr=True,
                        label=f"{entry_id}  (unparseable)",
                    )
                )
                continue
            short_id = entry_id if len(entry_id) <= 14 else entry_id[:14]
            pr = "pr" if parsed["auto_pr"] else "no-pr"
            label = (
                f"{short_id}  {parsed['repo']}/{parsed['ticket_name']}  "
                f"[{pr}]  {parsed['model']}"
            )
            rows.append(
                WorkorderRow(
                    entry_id=entry_id,
                    repo=parsed["repo"],
                    ticket_name=parsed["ticket_name"],
                    model=parsed["model"],
                    auto_pr=bool(parsed["auto_pr"]),
                    label=label,
                )
            )
        return rows

    def _poll(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and (now - self._last_poll) < POLL_INTERVAL_SEC:
            return
        self._last_poll = now

        client = self._connect_redis()
        docker_ok = self._probe_docker()
        self._containers = self._list_sandbox_containers() if docker_ok else []

        if client is None:
            self._workorders = []
            self._agent_handlers = []
            self._has_error = True
            self._refresh_widgets()
            return

        try:
            self._workorders = self._recent_workorders(client)
            self._agent_handlers = self._scan_agent_handlers(client)
            error_statuses = {STATUS_ERROR, STATUS_STARTUP_ERROR}
            self._has_error = any(
                h.status in error_statuses for h in self._agent_handlers
            )
        except RedisError:
            self._redis = None
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
            items = [w.label for w in self._workorders] or ["(no WORKORDER entries)"]
            dpg.configure_item(queue, items=items)

        live = self._tag("live_list")
        if dpg.does_item_exist(live):
            items = [h.label for h in self._agent_handlers] or ["(no live agent handlers)"]
            dpg.configure_item(live, items=items)

        dock = self._tag("docker_list")
        if dpg.does_item_exist(dock):
            items = self._containers or [f"(no {CONTAINER_NAME_PREFIX}* containers)"]
            dpg.configure_item(dock, items=items)

    def build_ui(
        self,
        parent: str,
        *,
        tag_prefix: str,
        width: int = 420,
        height: int = 120,
    ) -> None:
        """Fill the host content parent with MachineFactory monitor widgets."""
        self._root_tag = tag_prefix
        _ = width, height
        col_w = 128

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
                        width=col_w,
                    )
                with dpg.group():
                    dpg.add_text("Docker", color=COLOR_DIM)
                    dpg.add_listbox(
                        items=["(loading…)"],
                        tag=self._tag("docker_list"),
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
        _LIVE.pop(self._root_tag, None)


def build_ui(
    parent: str,
    *,
    tag_prefix: str,
    width: int = 420,
    height: int = 120,
) -> None:
    """Module-level builder for FeSpec / MegaDesk canvas hosting."""
    MachineFactoryFE().build_ui(
        parent,
        tag_prefix=tag_prefix,
        width=width,
        height=height,
    )


def main() -> None:
    raise SystemExit(
        "MachineFactory FE is canvas-only. Drop it from the MegaDesk Catalog."
    )


if __name__ == "__main__":
    main()
