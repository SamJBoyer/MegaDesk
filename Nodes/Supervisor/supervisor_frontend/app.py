"""Supervisor operator panel — catalog Send + running Stop via Redis streams."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

import dearpygui.dearpygui as dpg
from megadesk_contracts import discover_backends, ensure_supervisor_running, frame_pump

from backend.client import SupervisorStreamClient

COLOR_GREEN = (46, 204, 113, 255)
COLOR_RED = (231, 76, 60, 255)
COLOR_DIM = (120, 120, 130, 255)

_STATUS_POLL_S = 1.0
_PROCESS_LOG_TAIL = 80
_LIVE: dict[str, "SupervisorPanel"] = {}


class SupervisorPanel:
    def __init__(self) -> None:
        self._client: Optional[SupervisorStreamClient] = None
        self._root_tag = "primary"
        self._redis_ok = False
        self._backend_ok = False
        self._last_poll = 0.0
        self._status = ""
        self._log_lines: list[str] = []
        self._backends: list[str] = []
        self._selected_catalog: Optional[str] = None
        self._running: list[dict[str, str]] = []
        self._running_labels: list[str] = []
        self._selected_running_uid: Optional[str] = None
        self._frame_registered = False

    @property
    def client(self) -> SupervisorStreamClient:
        if self._client is None:
            self._client = SupervisorStreamClient()
        return self._client

    def _tag(self, suffix: str) -> str:
        return f"{self._root_tag}::{suffix}"

    def _append_log(self, text: str) -> None:
        self._log_lines.append(text)
        self._log_lines = self._log_lines[-80:]
        tag = self._tag("log")
        if dpg.does_item_exist(tag):
            dpg.set_value(tag, "\n".join(self._log_lines))

    def _set_process_log(self, text: str) -> None:
        tag = self._tag("process_log")
        if dpg.does_item_exist(tag):
            dpg.set_value(tag, text)

    def _dot_color(self, ok: bool) -> tuple[int, int, int, int]:
        return COLOR_GREEN if ok else COLOR_RED

    def _refresh_status_widgets(self) -> None:
        if dpg.does_item_exist(self._tag("redis_dot")):
            dpg.configure_item(
                self._tag("redis_dot"), color=self._dot_color(self._redis_ok)
            )
        if dpg.does_item_exist(self._tag("backend_dot")):
            dpg.configure_item(
                self._tag("backend_dot"), color=self._dot_color(self._backend_ok)
            )
        if dpg.does_item_exist(self._tag("status_lbl")):
            dpg.set_value(self._tag("status_lbl"), self._status)

    @staticmethod
    def _running_label(entry: dict[str, str]) -> str:
        endpoint = entry.get("node_endpoint") or "?"
        uid = entry.get("unique_id") or "?"
        pid = entry.get("PID") or "?"
        status = (entry.get("status") or "running").strip() or "running"
        short = uid if len(uid) <= 8 else uid[:8]
        if status == "exited":
            code = entry.get("exit_code") or "?"
            return f"{endpoint}  {short}…  exited={code}"
        return f"{endpoint}  {short}…  pid={pid}"

    @staticmethod
    def _tail_file(path: str, max_lines: int = _PROCESS_LOG_TAIL) -> str:
        if not path:
            return "(no log_path)"
        try:
            p = Path(path)
            if not p.is_file():
                return f"(log file missing: {path})"
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            return f"(failed to read log: {exc})"
        lines = text.splitlines()
        if len(lines) > max_lines:
            lines = lines[-max_lines:]
        return "\n".join(lines) if lines else "(empty log)"

    def _refresh_process_log(self) -> None:
        entry = self._selected_running_entry()
        if not entry:
            self._set_process_log("Select a running/exited instance to view its log.")
            return
        self._set_process_log(self._tail_file(entry.get("log_path") or ""))

    def _refresh_lists(self) -> None:
        catalog = self._tag("catalog")
        if dpg.does_item_exist(catalog):
            dpg.configure_item(
                catalog, items=self._backends or ["(no BE nodes discovered)"]
            )
        running = self._tag("running")
        if dpg.does_item_exist(running):
            self._running_labels = [self._running_label(e) for e in self._running]
            dpg.configure_item(
                running,
                items=self._running_labels or ["(none running)"],
            )
        self._refresh_process_log()

    def _poll_status(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and (now - self._last_poll) < _STATUS_POLL_S:
            return
        self._last_poll = now
        try:
            self._redis_ok = self.client.redis_ok()
            self._backend_ok = self.client.backend_ok() if self._redis_ok else False
            self._backends = sorted(
                name for name in discover_backends() if name != "supervisor"
            )
            self._running = self.client.list_running() if self._redis_ok else []
            live = sum(1 for e in self._running if (e.get("status") or "running") != "exited")
            exited = len(self._running) - live
            self._status = f"live={live} exited={exited}"
        except Exception as exc:  # noqa: BLE001 — UI must stay up if Redis is down
            self._redis_ok = False
            self._backend_ok = False
            self._running = []
            self._status = f"status error: {exc}"
        self._refresh_status_widgets()
        self._refresh_lists()

    def _ensure_backend(self) -> bool:
        if self.client.backend_ok():
            return True
        self._append_log("Starting supervisor backend…")
        ok = ensure_supervisor_running()
        self._poll_status(force=True)
        if ok:
            self._append_log("Backend running")
        else:
            self._append_log("Backend failed to start — see logs/supervisor/supervisor.log")
        return ok

    def build_ui(
        self,
        parent: str,
        *,
        tag_prefix: str,
        width: int = 520,
        height: int = 520,
    ) -> None:
        """Fill the host content parent with Supervisor widgets."""
        self._root_tag = tag_prefix
        _ = width, height

        with dpg.group(parent=parent):
            with dpg.group(horizontal=True):
                dpg.add_text("Redis:")
                dpg.add_text("*", tag=self._tag("redis_dot"), color=COLOR_RED)
                dpg.add_spacer(width=10)
                dpg.add_text("Backend:")
                dpg.add_text("*", tag=self._tag("backend_dot"), color=COLOR_RED)
                dpg.add_spacer(width=8)
                dpg.add_text("", tag=self._tag("status_lbl"), wrap=220)

            with dpg.group(horizontal=True):
                dpg.add_button(label="Send", width=70, callback=self._on_send)
                dpg.add_button(label="Stop", width=70, callback=self._on_stop)
                dpg.add_button(label="Stop all", width=70, callback=self._on_stop_all)
                dpg.add_spacer(width=8)
                dpg.add_button(
                    label="Refresh",
                    width=70,
                    callback=lambda: self._poll_status(force=True),
                )
                dpg.add_button(
                    label="Start BE",
                    width=70,
                    callback=lambda: self._ensure_backend(),
                )

            dpg.add_separator()
            dpg.add_text("Catalog", color=COLOR_DIM)
            dpg.add_listbox(
                items=["(loading…)"],
                tag=self._tag("catalog"),
                num_items=5,
                width=-1,
                callback=self._on_catalog_select,
            )

            dpg.add_separator()
            dpg.add_text("Running / exited", color=COLOR_DIM)
            dpg.add_listbox(
                items=["(none running)"],
                tag=self._tag("running"),
                num_items=5,
                width=-1,
                callback=self._on_running_select,
            )

            dpg.add_separator()
            dpg.add_text("Process log", color=COLOR_DIM)
            dpg.add_input_text(
                tag=self._tag("process_log"),
                default_value="Select a running/exited instance to view its log.",
                multiline=True,
                readonly=True,
                width=-1,
                height=140,
            )

            dpg.add_separator()
            dpg.add_text("Actions", color=COLOR_DIM)
            dpg.add_input_text(
                tag=self._tag("log"),
                default_value="",
                multiline=True,
                readonly=True,
                width=-1,
                height=-1,
            )

        dpg.set_item_user_data(parent, self.shutdown)
        self._ensure_backend()
        self._poll_status(force=True)
        if not self._frame_registered:
            frame_pump.register(self._on_frame)
            self._frame_registered = True
        _LIVE[tag_prefix] = self

    def _on_frame(self) -> None:
        if not dpg.does_item_exist(self._root_tag):
            return
        self._poll_status()

    def shutdown(self) -> None:
        if self._frame_registered:
            frame_pump.unregister(self._on_frame)
            self._frame_registered = False
        _LIVE.pop(self._root_tag, None)

    def _on_catalog_select(self, sender, app_data, user_data=None) -> None:
        name = str(app_data if app_data is not None else "").strip()
        if name and name in self._backends:
            self._selected_catalog = name
        else:
            self._selected_catalog = None

    def _on_running_select(self, sender, app_data, user_data=None) -> None:
        label = str(app_data if app_data is not None else "").strip()
        self._selected_running_uid = None
        for entry, entry_label in zip(self._running, self._running_labels):
            if entry_label == label:
                self._selected_running_uid = entry.get("unique_id")
                self._refresh_process_log()
                return

    def _selected_catalog_name(self) -> Optional[str]:
        if self._selected_catalog and self._selected_catalog in self._backends:
            return self._selected_catalog
        tag = self._tag("catalog")
        if dpg.does_item_exist(tag):
            name = str(dpg.get_value(tag) or "").strip()
            if name in self._backends:
                return name
        return None

    def _selected_running_entry(self) -> Optional[dict[str, str]]:
        uid = self._selected_running_uid
        if uid:
            for entry in self._running:
                if entry.get("unique_id") == uid:
                    return entry
        tag = self._tag("running")
        if dpg.does_item_exist(tag):
            label = str(dpg.get_value(tag) or "").strip()
            for entry, entry_label in zip(self._running, self._running_labels):
                if entry_label == label:
                    return entry
        return None

    def _on_send(self) -> None:
        if not self._ensure_backend():
            self._append_log("Send: backend not running")
            return
        name = self._selected_catalog_name()
        if not name:
            self._append_log("Send: select a catalog BE node")
            return
        entry_id = self.client.launch_node(name, parameters="")
        self._append_log(f"LAUNCHREQUEST {name} -> {entry_id}")
        self._poll_status(force=True)

    def _on_stop(self) -> None:
        if not self.client.backend_ok():
            self._append_log("Stop: backend not running")
            return
        entry = self._selected_running_entry()
        if not entry:
            self._append_log("Stop: select a running node")
            return
        endpoint = entry.get("node_endpoint") or ""
        uid = entry.get("unique_id") or ""
        if not endpoint or not uid:
            self._append_log("Stop: incomplete RUNNINGNODES entry")
            return
        entry_id = self.client.kill_node(endpoint, uid)
        self._append_log(f"KILLREQUEST {endpoint} {uid[:8]}… -> {entry_id}")
        self._selected_running_uid = None
        self._poll_status(force=True)

    def _on_stop_all(self) -> None:
        self._poll_status(force=True)
        if not self._redis_ok:
            self._append_log("Stop all: Redis not connected")
            return
        n = self.client.kill_all_running()
        self._append_log(f"KILLREQUEST queued for {n} running node(s)")
        self._selected_running_uid = None
        self._poll_status(force=True)


def build_ui(
    parent: str,
    *,
    tag_prefix: str,
    width: int = 520,
    height: int = 520,
) -> None:
    """Module-level builder for FeSpec / MegaDesk canvas hosting."""
    SupervisorPanel().build_ui(
        parent,
        tag_prefix=tag_prefix,
        width=width,
        height=height,
    )


def main() -> None:
    raise SystemExit(
        "Supervisor FE is canvas-only. Drop it from the MegaDesk Catalog."
    )


if __name__ == "__main__":
    main()
