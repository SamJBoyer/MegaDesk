"""Work Dispatcher — a human gate that hands labeled git issues to a factory."""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from typing import Mapping, Optional

import dearpygui.dearpygui as dpg
import redis
from megadesk_contracts import (
    coerce_parameters,
    frame_pump,
    redis_connect,
    resolve_ephemeral_db,
    resolve_redis_url,
)
from megadesk_contracts.human_gate import (
    LABEL_AGENT_READY,
    check_repo,
    list_labeled_issues,
    list_repo_labels,
    normalize_repo_url,
    parse_github_repo,
    run_gh,
)
from megadesk_contracts.wire.cloud import (
    CLOUDORDER_STREAM,
    cloudorder_fields,
    new_order_id,
)
from megadesk_contracts.wire.machine import (
    DEFAULT_MODEL,
    WORKORDER_STREAM,
    workorder_fields,
)

POLL_INTERVAL_SEC = 3.0
MODEL_OPTIONS = ("auto", "grok-4.6", "claude-opus-5")
FACTORY_OPTIONS = ("machine", "cloud")
DEFAULT_FACTORY = "machine"
DEFAULT_LABEL = LABEL_AGENT_READY

# Graph parameters this node recognizes; declared in parameters.yaml.
PARAM_GIT_URL = "GIT_URL"
PARAM_ISSUE_LABEL = "ISSUE_LABEL"

COLOR_GREEN = (80, 200, 80, 255)
COLOR_RED = (220, 70, 70, 255)
COLOR_BLUE = (70, 140, 230, 255)
COLOR_DIM = (90, 90, 90, 255)

# Keep live instances alive while their embed windows exist.
_LIVE: dict[str, "WorkDispatcher"] = {}


@dataclass
class IssueTicket:
    id: int
    name: str
    body: str
    url: str


class WorkDispatcher:
    def __init__(self, parameters: Optional[Mapping[str, str]] = None) -> None:
        self._ui_queue: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._poll_thread: Optional[threading.Thread] = None
        self._state_lock = threading.Lock()
        values = coerce_parameters(parameters)
        # GIT_URL from the graph: the repo this instance boots pointed at.
        self._current_repo_url = values.get(PARAM_GIT_URL, "").strip()
        self._label = values.get(PARAM_ISSUE_LABEL, "").strip() or DEFAULT_LABEL
        self._labels_for = ""
        self._tickets: dict[int, IssueTicket] = {}
        self._dispatched: set[int] = set()
        self._redis: Optional[redis.Redis] = None
        self.redis_url = resolve_redis_url()
        self._root_tag = "primary"
        self._frame_registered = False
        self._connect_redis()

    def _tag(self, suffix: str) -> str:
        return f"{self._root_tag}::{suffix}"

    def _connect_redis(self) -> None:
        try:
            self._redis = redis_connect(
                self.redis_url,
                db=resolve_ephemeral_db(self.redis_url),
                socket_connect_timeout=2,
            )
            self._redis.ping()
        except (redis.RedisError, OSError, ValueError):
            self._redis = None

    def build_ui(
        self,
        parent: str,
        *,
        tag_prefix: str,
        width: int = 480,
        height: int = 160,
    ) -> None:
        """Fill the host content parent with Work Dispatcher widgets."""
        self._root_tag = tag_prefix
        _ = width
        # Compact chrome: URL + label/status ≈ 48px; list starts at ~2 rows and grows.
        self._row_h = 26
        self._scroll_max = max(self._row_h * 2, height - 48) if height else None

        theme_tag = self._tag("ticket_theme")
        if not dpg.does_item_exist(theme_tag):
            with dpg.theme(tag=theme_tag):
                with dpg.theme_component(dpg.mvButton):
                    dpg.add_theme_color(dpg.mvThemeCol_Button, (210, 215, 225, 255))
                    dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (190, 198, 210, 255))
                    dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (175, 185, 200, 255))
                    dpg.add_theme_color(dpg.mvThemeCol_Text, (30, 32, 38, 255))
                    dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 3)
                    dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 6, 3)

        with dpg.group(parent=parent):
            with dpg.group(horizontal=True):
                dpg.add_input_text(
                    tag=self._tag("git_url"),
                    default_value=self._current_repo_url,
                    width=-20,
                    hint="https://github.com/owner/repo",
                    callback=self._on_url_changed,
                    on_enter=True,
                )
                with dpg.drawlist(width=16, height=16, tag=self._tag("conn_light_dl")):
                    dpg.draw_circle(
                        (8, 8),
                        6,
                        fill=COLOR_DIM,
                        color=COLOR_DIM,
                        tag=self._tag("conn_light"),
                    )

            with dpg.group(horizontal=True):
                dpg.add_combo(
                    items=[self._label],
                    default_value=self._label,
                    width=150,
                    height_mode=dpg.mvComboHeight_Small,
                    tag=self._tag("label_combo"),
                    callback=self._on_label_changed,
                )
                dpg.add_text(
                    "Idle", tag=self._tag("status_text"), color=(160, 160, 160)
                )
            dpg.add_child_window(
                tag=self._tag("ticket_scroll"),
                width=-1,
                height=self._row_h * 2,
                border=True,
            )

        dpg.set_item_user_data(parent, self.shutdown)
        self._start_services()
        _LIVE[tag_prefix] = self

    def _start_services(self) -> None:
        if self._poll_thread and self._poll_thread.is_alive():
            return
        self._stop.clear()
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()
        if not self._frame_registered:
            frame_pump.register(self._on_frame)
            self._frame_registered = True

    def _on_frame(self) -> None:
        if not dpg.does_item_exist(self._root_tag):
            return
        self._sync_url_from_input()
        self._drain_ui_queue()

    def shutdown(self) -> None:
        self._stop.set()
        if self._frame_registered:
            frame_pump.unregister(self._on_frame)
            self._frame_registered = False
        if self._poll_thread:
            self._poll_thread.join(timeout=2.0)
            self._poll_thread = None
        _LIVE.pop(self._root_tag, None)

    def _set_conn_light(self, color: tuple[int, int, int, int]) -> None:
        tag = self._tag("conn_light")
        if dpg.does_item_exist(tag):
            dpg.configure_item(tag, fill=color, color=color)

    def _sync_url_from_input(self) -> None:
        tag = self._tag("git_url")
        if not dpg.does_item_exist(tag):
            return
        with self._state_lock:
            self._current_repo_url = dpg.get_value(tag).strip()

    def _on_url_changed(self, sender, app_data, user_data=None) -> None:
        self._sync_url_from_input()
        self._ui_queue.put(("status", "Checking remote…", (180, 180, 100)))

    def _on_label_changed(self, sender, app_data, user_data=None) -> None:
        """Retarget the gate: a different label is a different queue of work."""
        chosen = (app_data or "").strip() or DEFAULT_LABEL
        with self._state_lock:
            self._label = chosen
        self._clear_rows()
        self._ui_queue.put(("status", f"Tracking {chosen}…", (180, 180, 100)))

    def _clear_rows(self) -> None:
        for ticket_id in list(self._tickets):
            row = self._tag(f"ticket_row_{ticket_id}")
            if dpg.does_item_exist(row):
                dpg.delete_item(row)
        self._tickets.clear()
        self._resize_ticket_scroll()

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            with self._state_lock:
                url = self._current_repo_url
                label = self._label

            if not url:
                self._ui_queue.put(("conn", False))
                self._ui_queue.put(
                    ("status", "Enter a GitHub repository URL", (160, 160, 160))
                )
                self._stop.wait(POLL_INTERVAL_SEC)
                continue

            parsed = parse_github_repo(url)
            if not parsed:
                self._ui_queue.put(("conn", False))
                self._ui_queue.put(
                    (
                        "status",
                        "Unsupported URL (GitHub https or SSH required)",
                        COLOR_RED,
                    )
                )
                self._stop.wait(POLL_INTERVAL_SEC)
                continue

            owner, repo = parsed
            repo_url = normalize_repo_url(url, owner, repo)
            ok, issues, err = self._fetch_labeled(owner, repo, label)

            if not ok:
                self._ui_queue.put(("conn", False))
                self._ui_queue.put(("status", err or "Connection failed", COLOR_RED))
            else:
                self._ui_queue.put(("conn", True))
                self._ui_queue.put(
                    (
                        "status",
                        f"Connected — {len(issues)} {label} issue(s)",
                        COLOR_GREEN,
                    )
                )
                for issue in issues:
                    issue.url = repo_url
                    self._ui_queue.put(("ticket", issue))

            self._stop.wait(POLL_INTERVAL_SEC)

    def _refresh_labels(self, owner: str, repo: str) -> None:
        """Offer the repo's own labels once per repo, so the target is real."""
        slug = f"{owner}/{repo}"
        if self._labels_for == slug:
            return
        ok, labels, _err = list_repo_labels(owner, repo, gh=run_gh)
        if not ok:
            return
        self._labels_for = slug
        self._ui_queue.put(("labels", labels))

    def _fetch_labeled(
        self, owner: str, repo: str, label: str
    ) -> tuple[bool, list[IssueTicket], Optional[str]]:
        ok, err = check_repo(owner, repo, gh=run_gh)
        if not ok:
            return False, [], err

        self._refresh_labels(owner, repo)

        ok, issues, err = list_labeled_issues(owner, repo, label, gh=run_gh)
        if not ok:
            return False, [], err

        return (
            True,
            [
                IssueTicket(id=issue.number, name=issue.title, body=issue.body, url="")
                for issue in issues
            ],
            None,
        )

    def _drain_ui_queue(self) -> None:
        while True:
            try:
                msg = self._ui_queue.get_nowait()
            except queue.Empty:
                break

            kind = msg[0]
            if kind == "conn":
                self._set_conn_light(COLOR_GREEN if msg[1] else COLOR_RED)
            elif kind == "status":
                _, text, color = msg
                status = self._tag("status_text")
                if dpg.does_item_exist(status):
                    dpg.set_value(status, text)
                    dpg.configure_item(status, color=color)
            elif kind == "labels":
                self._set_label_items(msg[1])
            elif kind == "ticket":
                self._ensure_ticket_row(msg[1])

    def _set_label_items(self, labels: list[str]) -> None:
        tag = self._tag("label_combo")
        if not dpg.does_item_exist(tag):
            return
        items = list(labels)
        if self._label not in items:
            items.insert(0, self._label)
        dpg.configure_item(tag, items=items)
        dpg.set_value(tag, self._label)

    def _resize_ticket_scroll(self) -> None:
        scroll = self._tag("ticket_scroll")
        if not dpg.does_item_exist(scroll):
            return
        n = max(2, len(self._tickets))
        h = n * self._row_h
        if self._scroll_max is not None:
            h = min(h, self._scroll_max)
        dpg.configure_item(scroll, height=h)

    def _ensure_ticket_row(self, ticket: IssueTicket) -> None:
        if ticket.id in self._tickets:
            self._tickets[ticket.id] = ticket
            return

        self._tickets[ticket.id] = ticket
        light_tag = self._tag(f"ticket_light_{ticket.id}")
        row_tag = self._tag(f"ticket_row_{ticket.id}")
        scroll = self._tag("ticket_scroll")

        factory_tag = self._tag(f"ticket_factory_{ticket.id}")
        model_tag = self._tag(f"ticket_model_{ticket.id}")
        with dpg.group(parent=scroll, horizontal=True, tag=row_tag):
            with dpg.drawlist(width=16, height=16):
                dpg.draw_circle(
                    (8, 8),
                    6,
                    fill=COLOR_GREEN,
                    color=COLOR_GREEN,
                    tag=light_tag,
                )
            btn = dpg.add_button(
                label=ticket.name,
                width=-250,
                height=22,
                tag=self._tag(f"ticket_btn_{ticket.id}"),
                user_data=ticket.id,
                callback=self._on_ticket_pressed,
            )
            dpg.bind_item_theme(btn, self._tag("ticket_theme"))
            dpg.add_combo(
                items=list(FACTORY_OPTIONS),
                default_value=DEFAULT_FACTORY,
                width=90,
                height_mode=dpg.mvComboHeight_Small,
                tag=factory_tag,
            )
            dpg.add_combo(
                items=list(MODEL_OPTIONS),
                default_value=DEFAULT_MODEL,
                width=140,
                height_mode=dpg.mvComboHeight_Small,
                tag=model_tag,
            )

        self._resize_ticket_scroll()

    def _on_ticket_pressed(self, sender, app_data, user_data: int) -> None:
        issue_id = user_data
        ticket = self._tickets.get(issue_id)
        if ticket is None:
            return

        light_tag = self._tag(f"ticket_light_{issue_id}")
        if dpg.does_item_exist(light_tag):
            dpg.configure_item(light_tag, fill=COLOR_BLUE, color=COLOR_BLUE)

        with self._state_lock:
            fallback_url = self._current_repo_url
        repo_url = ticket.url or fallback_url
        parsed = parse_github_repo(repo_url)
        if parsed:
            owner, repo = parsed
            repo_url = normalize_repo_url(repo_url, owner, repo)
            repo_name = repo
        else:
            repo_name = repo_url.rstrip("/").rsplit("/", 1)[-1]
            if repo_name.endswith(".git"):
                repo_name = repo_name[:-4]

        model = DEFAULT_MODEL
        model_tag = self._tag(f"ticket_model_{issue_id}")
        if dpg.does_item_exist(model_tag):
            model = (dpg.get_value(model_tag) or "").strip() or DEFAULT_MODEL

        factory = DEFAULT_FACTORY
        factory_tag = self._tag(f"ticket_factory_{issue_id}")
        if dpg.does_item_exist(factory_tag):
            factory = (dpg.get_value(factory_tag) or "").strip() or DEFAULT_FACTORY

        status = self._tag("status_text")
        instructions = ticket.body or ticket.name
        try:
            if factory == "machine":
                stream = WORKORDER_STREAM
                fields = workorder_fields(
                    repo=repo_name,
                    url=repo_url,
                    ticket_name=ticket.name,
                    instructions=instructions,
                    model=model,
                    auto_pr=True,
                )
            elif factory == "cloud":
                stream = CLOUDORDER_STREAM
                fields = cloudorder_fields(
                    order_id=new_order_id(),
                    repo_url=repo_url,
                    title=ticket.name,
                    instructions=instructions,
                    model=model,
                    auto_pr=True,
                )
            else:
                raise ValueError(f"unknown factory {factory!r}")
        except ValueError as exc:
            if dpg.does_item_exist(status):
                dpg.set_value(status, f"Cannot dispatch #{issue_id}: {exc}")
                dpg.configure_item(status, color=COLOR_RED)
            return

        if self._redis is None:
            self._connect_redis()

        if self._redis is None:
            if dpg.does_item_exist(status):
                dpg.set_value(status, "Redis unavailable — could not dispatch")
                dpg.configure_item(status, color=COLOR_RED)
            return

        try:
            self._redis.xadd(stream, fields)
            self._dispatched.add(issue_id)
            if dpg.does_item_exist(status):
                dpg.set_value(status, f"Dispatched #{issue_id} → {stream}")
                dpg.configure_item(status, color=COLOR_BLUE)
        except redis.RedisError as exc:
            if dpg.does_item_exist(status):
                dpg.set_value(status, f"Redis xadd failed: {exc}")
                dpg.configure_item(status, color=COLOR_RED)
            self._redis = None


def build_ui(
    parent: str,
    *,
    tag_prefix: str,
    width: int = 480,
    height: int = 160,
    parameters: Optional[Mapping[str, str]] = None,
) -> None:
    """Module-level builder for FeSpec / MegaDesk graph hosting."""
    WorkDispatcher(parameters).build_ui(
        parent,
        tag_prefix=tag_prefix,
        width=width,
        height=height,
    )


def read_parameters(tag_prefix: str) -> dict[str, str]:
    """Current parameter values of the instance hosted under ``tag_prefix``.

    Reads the widgets rather than the instance's own fields, so what the graph
    captures is what the operator can see.
    """
    url_tag = f"{tag_prefix}::git_url"
    if not dpg.does_item_exist(url_tag):
        return {}
    label_tag = f"{tag_prefix}::label_combo"
    label = (
        (dpg.get_value(label_tag) or "").strip()
        if dpg.does_item_exist(label_tag)
        else ""
    )
    return {
        PARAM_GIT_URL: (dpg.get_value(url_tag) or "").strip(),
        PARAM_ISSUE_LABEL: label or DEFAULT_LABEL,
    }


def main() -> None:
    raise SystemExit(
        "Work Dispatcher FE is graph-hosted. Drop it from the MegaDesk Catalog."
    )


if __name__ == "__main__":
    main()
