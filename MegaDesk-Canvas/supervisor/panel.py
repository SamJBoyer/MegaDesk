"""Supervisor operator panel — collapsible canvas chrome (not a Catalog node)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

import dearpygui.dearpygui as dpg
from megadesk_contracts import SupervisorClient, discover_backends, ensure_supervisor_running, frame_pump
from megadesk_contracts.log_session import session_log_path

COLOR_GREEN = (46, 204, 113, 255)
COLOR_RED = (231, 76, 60, 255)
COLOR_DIM = (120, 120, 130, 255)

SUPERVISOR_PANEL_TAG = "supervisor_panel_window"
SUPERVISOR_BODY_TAG = "supervisor_panel_window::body"
_STATUS_POLL_S = 1.0
_PROCESS_LOG_TAIL = 200
_LIVE: dict[str, "SupervisorPanel"] = {}


class SupervisorPanel:
    def __init__(self) -> None:
        self._client: Optional[SupervisorClient] = None
        self._root_tag = "supervisor_panel"
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
        self._log_endpoint: Optional[str] = None
        self._frame_registered = False
        self._pending_logs_tab = False

    @property
    def client(self) -> SupervisorClient:
        if self._client is None:
            self._client = SupervisorClient()
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
        short = uid if len(uid) <= 8 else uid[:8]
        hb_pid = (entry.get("node_pid") or "").strip()
        if hb_pid and hb_pid != pid:
            return f"{endpoint}  {short}…  pid={hb_pid}"
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

    def _session_log_text(self, endpoint: str) -> str:
        try:
            path = str(session_log_path(endpoint))
        except Exception as exc:
            return f"(no log for {endpoint}: {exc})"
        return self._tail_file(path)

    def _refresh_process_log(self) -> None:
        entry = self._selected_running_entry()
        if entry:
            self._set_process_log(self._tail_file(entry.get("log_path") or ""))
            return
        if self._log_endpoint:
            self._set_process_log(self._session_log_text(self._log_endpoint))
            return
        self._set_process_log("Select an alive instance to view its log.")

    def _restore_running_selection(self) -> None:
        tag = self._tag("running")
        if not dpg.does_item_exist(tag) or not self._selected_running_uid:
            return
        for entry, label in zip(self._running, self._running_labels):
            if entry.get("unique_id") == self._selected_running_uid:
                dpg.set_value(tag, label)
                return

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
            self._restore_running_selection()
        self._refresh_process_log()

    def _poll_status(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and (now - self._last_poll) < _STATUS_POLL_S:
            return
        self._last_poll = now
        try:
            self._redis_ok = self.client.redis_ok()
            self._backend_ok = self.client.backend_ok() if self._redis_ok else False
            self._backends = sorted(discover_backends())
            self._running = self.client.list_running() if self._redis_ok else []
            self._status = f"alive={len(self._running)}"
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
            try:
                hint = str(session_log_path("supervisor"))
            except Exception:
                hint = "Logs/CURRENT → supervisor.md"
            self._append_log(f"Backend failed to start — see {hint}")
        return ok

    def build_ui(
        self,
        parent: str,
        *,
        tag_prefix: str,
        width: int = 520,
        height: int = 520,
    ) -> None:
        """Fill a parent with Supervisor widgets."""
        self._root_tag = tag_prefix
        _ = width, height

        with dpg.tab_bar(parent=parent, tag=self._tag("tabs")):
            with dpg.tab(label="Nodes", tag=self._tag("tab_nodes")):
                with dpg.group(horizontal=True):
                    dpg.add_text("Redis:")
                    dpg.add_text("*", tag=self._tag("redis_dot"), color=COLOR_RED)
                    dpg.add_spacer(width=10)
                    dpg.add_text("Backend:")
                    dpg.add_text("*", tag=self._tag("backend_dot"), color=COLOR_RED)
                    dpg.add_spacer(width=8)
                    dpg.add_text("", tag=self._tag("status_lbl"), wrap=180)

                with dpg.group(horizontal=True):
                    dpg.add_button(label="Send", width=70, callback=self._on_send)
                    dpg.add_button(label="Stop", width=70, callback=self._on_stop)
                    dpg.add_button(label="Stop all", width=70, callback=self._on_stop_all)

                with dpg.group(horizontal=True):
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
                dpg.add_text("Alive procs", color=COLOR_DIM)
                dpg.add_listbox(
                    items=["(none running)"],
                    tag=self._tag("running"),
                    num_items=5,
                    width=-1,
                    callback=self._on_running_select,
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

            with dpg.tab(label="Logs", tag=self._tag("tab_logs")):
                with dpg.child_window(
                    tag=self._tag("log_host"),
                    width=-1,
                    height=-1,
                    border=False,
                    no_scrollbar=True,
                ):
                    dpg.add_input_text(
                        tag=self._tag("process_log"),
                        default_value="Select an alive instance to view its log.",
                        multiline=True,
                        readonly=True,
                        width=-1,
                        height=-1,
                    )

        dpg.set_item_user_data(parent, self.shutdown)
        self._poll_status(force=True)
        if not self._frame_registered:
            frame_pump.register(self._on_frame)
            self._frame_registered = True
        _LIVE[tag_prefix] = self

    def _on_frame(self) -> None:
        if not dpg.does_item_exist(SUPERVISOR_PANEL_TAG) and not dpg.does_item_exist(
            self._root_tag
        ):
            return
        self._poll_status()
        if self._pending_logs_tab:
            self._apply_logs_tab()
            self._pending_logs_tab = False

    def shutdown(self) -> None:
        if self._frame_registered:
            frame_pump.unregister(self._on_frame)
            self._frame_registered = False
        _LIVE.pop(self._root_tag, None)

    def _apply_logs_tab(self) -> None:
        tabs = self._tag("tabs")
        logs = self._tag("tab_logs")
        if dpg.does_item_exist(tabs) and dpg.does_item_exist(logs):
            dpg.set_value(tabs, logs)

    def _focus_logs_tab(self) -> None:
        self._pending_logs_tab = True
        self._apply_logs_tab()

    def show_logs_for_node(
        self,
        name: str,
        backends: tuple[str, ...] = (),
        *,
        focus: bool = True,
    ) -> None:
        """Show the session / RUNNINGNODES log for a canvas node."""
        endpoints: list[str] = []
        for item in backends:
            if item and item not in endpoints:
                endpoints.append(item)
        if name and name not in endpoints:
            endpoints.append(name)

        entry = None
        for endpoint in endpoints:
            for running in self._running:
                if running.get("node_endpoint") == endpoint:
                    entry = running
                    break
            if entry is not None:
                break

        if entry is not None:
            self._selected_running_uid = entry.get("unique_id")
            self._log_endpoint = None
            self._restore_running_selection()
            self._refresh_process_log()
        else:
            self._selected_running_uid = None
            self._log_endpoint = endpoints[0] if endpoints else name
            self._refresh_process_log()
        if focus:
            self._focus_logs_tab()

    def _on_catalog_select(self, sender, app_data, user_data=None) -> None:
        name = str(app_data if app_data is not None else "").strip()
        if name and name in self._backends:
            self._selected_catalog = name
        else:
            self._selected_catalog = None

    def _on_running_select(self, sender, app_data, user_data=None) -> None:
        label = str(app_data if app_data is not None else "").strip()
        self._selected_running_uid = None
        self._log_endpoint = None
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


def live_panel() -> Optional[SupervisorPanel]:
    panel = _LIVE.get(SUPERVISOR_PANEL_TAG)
    if panel is not None:
        return panel
    if _LIVE:
        return next(iter(_LIVE.values()))
    return None


def show_logs_for_canvas_node(name: str, backends: tuple[str, ...] = ()) -> None:
    """Canvas selection hook: show this node's log in the Supervisor Logs tab."""
    panel = live_panel()
    if panel is None:
        return
    panel.show_logs_for_node(name, backends, focus=True)


def build_supervisor_panel(
    parent: str | None = None,
    *,
    width: int = 360,
    height: int = 560,
) -> SupervisorPanel:
    """Fill the docked Supervisor pane (created by the canvas chrome)."""
    target = parent or SUPERVISOR_BODY_TAG
    if not dpg.does_item_exist(target):
        raise RuntimeError(
            f"Supervisor pane {target!r} missing; canvas chrome must create it first"
        )
    if dpg.does_item_exist(target):
        dpg.delete_item(target, children_only=True)
    for panel in list(_LIVE.values()):
        panel.shutdown()

    panel = SupervisorPanel()
    panel.build_ui(
        target,
        tag_prefix=SUPERVISOR_PANEL_TAG,
        width=width,
        height=height,
    )
    return panel
