"""Supervisor operator panel — catalog Send + running Stop via Redis streams."""

from __future__ import annotations

import time
from typing import Callable, Optional

import dearpygui.dearpygui as dpg
from megadesk import discover_backends, ensure_supervisor_running, frame_pump

from backend.client import SupervisorStreamClient

COLOR_GREEN = (46, 204, 113, 255)
COLOR_RED = (231, 76, 60, 255)
COLOR_DIM = (120, 120, 130, 255)

_STATUS_POLL_S = 1.0
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
        self._owns_context = False

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
        return f"{endpoint}  {short}…  pid={pid}"

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
            self._status = f"running={len(self._running)}"
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
            self._append_log("Backend failed to start")
        return ok

    def build_ui(
        self,
        tag: str = "primary",
        *,
        pos: Optional[tuple[float, float]] = None,
        on_close: Optional[Callable[[], None]] = None,
        width: int = 520,
        height: int = 520,
        no_move: bool = False,
        no_resize: bool = False,
        no_title_bar: bool = False,
    ) -> str:
        self._root_tag = tag
        if dpg.does_item_exist(tag):
            dpg.delete_item(tag)

        kwargs: dict = {}
        if pos is not None:
            kwargs["pos"] = list(pos)

        def _close() -> None:
            self.shutdown()
            if on_close:
                on_close()

        # Hosted shells (no_title_bar) close via canvas chrome + user_data, not DPG X.
        use_dpg_close = on_close is not None and not no_title_bar
        with dpg.window(
            tag=tag,
            label="Supervisor",
            width=width,
            height=height,
            no_collapse=True,
            no_move=no_move,
            no_resize=no_resize,
            no_title_bar=no_title_bar,
            on_close=_close if use_dpg_close else None,
            no_close=not use_dpg_close,
            **kwargs,
        ):
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
                num_items=6,
                width=-1,
                callback=self._on_catalog_select,
            )

            dpg.add_separator()
            dpg.add_text("Running", color=COLOR_DIM)
            dpg.add_listbox(
                items=["(none running)"],
                tag=self._tag("running"),
                num_items=5,
                width=-1,
                callback=self._on_running_select,
            )

            dpg.add_separator()
            dpg.add_text("Log", color=COLOR_DIM)
            dpg.add_input_text(
                tag=self._tag("log"),
                default_value="",
                multiline=True,
                readonly=True,
                width=-1,
                height=-1,
            )

        dpg.set_item_user_data(tag, _close)
        self._ensure_backend()
        self._poll_status(force=True)
        if not self._frame_registered:
            frame_pump.register(self._on_frame)
            self._frame_registered = True
        _LIVE[tag] = self
        return tag

    def _on_frame(self) -> None:
        if not dpg.does_item_exist(self._root_tag):
            return
        self._poll_status()

    def shutdown(self) -> None:
        if self._frame_registered:
            frame_pump.unregister(self._on_frame)
            self._frame_registered = False
        _LIVE.pop(self._root_tag, None)

    def start(self) -> None:
        self._owns_context = True
        dpg.create_context()
        self.build_ui("primary")
        dpg.create_viewport(title="Supervisor", width=560, height=560)
        dpg.setup_dearpygui()
        dpg.show_viewport()
        dpg.set_primary_window("primary", True)
        while dpg.is_dearpygui_running():
            self._poll_status()
            dpg.render_dearpygui_frame()
        self.shutdown()
        dpg.destroy_context()

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
        self._poll_status(force=True)

    def _on_stop_all(self) -> None:
        self._poll_status(force=True)
        if not self._redis_ok:
            self._append_log("Stop all: Redis not connected")
            return
        n = self.client.kill_all_running()
        self._append_log(f"KILLREQUEST queued for {n} running node(s)")
        self._poll_status(force=True)


def build_ui(
    tag: str,
    *,
    pos: Optional[tuple[float, float]] = None,
    on_close: Optional[Callable[[], None]] = None,
    width: int = 520,
    height: int = 520,
    no_move: bool = False,
    no_resize: bool = False,
    no_title_bar: bool = False,
) -> str:
    """Module-level builder for FeSpec / MegaDesk canvas hosting."""
    return SupervisorPanel().build_ui(
        tag,
        pos=pos,
        on_close=on_close,
        width=width,
        height=height,
        no_move=no_move,
        no_resize=no_resize,
        no_title_bar=no_title_bar,
    )


def main() -> None:
    SupervisorPanel().start()


if __name__ == "__main__":
    main()
