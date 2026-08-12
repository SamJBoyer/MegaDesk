"""Ticket Dispatcher — visualize agent-ready git issues and dispatch them via Redis."""

from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import threading
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

import dearpygui.dearpygui as dpg
import redis
from megadesk_contracts import frame_pump

POLL_INTERVAL_SEC = 3.0
DEFAULT_REDIS_URL = "redis://localhost:6379/0"
REDIS_STREAM_KEY = "WORKORDER"
DEFAULT_MODEL = "auto"
MODEL_OPTIONS = ("auto", "grok-4.5")
GH_TIMEOUT_SEC = 15

COLOR_GREEN = (80, 200, 80, 255)
COLOR_RED = (220, 70, 70, 255)
COLOR_BLUE = (70, 140, 230, 255)
COLOR_DIM = (90, 90, 90, 255)

# Keep live instances alive while their embed windows exist.
_LIVE: dict[str, "TicketDispatcher"] = {}


@dataclass
class IssueTicket:
    id: int
    name: str
    body: str
    url: str


def parse_github_repo(git_url: str) -> Optional[tuple[str, str]]:
    """Extract (owner, repo) from common GitHub URL forms."""
    url = git_url.strip()
    if not url:
        return None

    ssh = re.match(r"git@github\.com:([^/]+)/([^/]+?)(?:\.git)?/?$", url)
    if ssh:
        return ssh.group(1), ssh.group(2)

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    parsed = urlparse(url)
    if parsed.hostname not in ("github.com", "www.github.com"):
        return None

    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) < 2:
        return None

    owner, repo = parts[0], parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    return owner, repo


def normalize_repo_url(git_url: str, owner: str, repo: str) -> str:
    return f"https://github.com/{owner}/{repo}"


def run_gh(*args: str) -> tuple[bool, str, str]:
    try:
        result = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            timeout=GH_TIMEOUT_SEC,
            check=False,
        )
    except FileNotFoundError:
        return False, "", "gh CLI not found — install and authenticate GitHub CLI"
    except subprocess.TimeoutExpired:
        return False, "", "gh command timed out"

    if result.returncode != 0:
        err = (result.stderr or result.stdout or "gh command failed").strip()
        return False, result.stdout, err
    return True, result.stdout, ""


class TicketDispatcher:
    def __init__(self) -> None:
        self._ui_queue: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._poll_thread: Optional[threading.Thread] = None
        self._url_lock = threading.Lock()
        self._current_repo_url = ""
        self._tickets: dict[int, IssueTicket] = {}
        self._dispatched: set[int] = set()
        self._redis: Optional[redis.Redis] = None
        self.redis_url = os.environ.get("REDIS_URL", DEFAULT_REDIS_URL)
        self._root_tag = "primary"
        self._frame_registered = False
        self._connect_redis()

    def _tag(self, suffix: str) -> str:
        return f"{self._root_tag}::{suffix}"

    def _connect_redis(self) -> None:
        try:
            self._redis = redis.Redis.from_url(
                self.redis_url,
                decode_responses=True,
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
        """Fill the host content parent with Ticket Dispatcher widgets."""
        self._root_tag = tag_prefix
        _ = width
        # Compact chrome: URL + status ≈ 48px; list starts at ~2 rows and grows.
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
        with self._url_lock:
            self._current_repo_url = dpg.get_value(tag).strip()

    def _on_url_changed(self, sender, app_data, user_data=None) -> None:
        self._sync_url_from_input()
        self._ui_queue.put(("status", "Checking remote…", (180, 180, 100)))

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            with self._url_lock:
                url = self._current_repo_url

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
            ok, issues, err = self._fetch_agent_ready(owner, repo)

            if not ok:
                self._ui_queue.put(("conn", False))
                self._ui_queue.put(("status", err or "Connection failed", COLOR_RED))
            else:
                self._ui_queue.put(("conn", True))
                self._ui_queue.put(
                    (
                        "status",
                        f"Connected — {len(issues)} agent-ready issue(s)",
                        COLOR_GREEN,
                    )
                )
                for issue in issues:
                    issue.url = repo_url
                    self._ui_queue.put(("ticket", issue))

            self._stop.wait(POLL_INTERVAL_SEC)

    def _fetch_agent_ready(
        self, owner: str, repo: str
    ) -> tuple[bool, list[IssueTicket], Optional[str]]:
        repo_slug = f"{owner}/{repo}"

        ok, _, err = run_gh("repo", "view", repo_slug, "--json", "nameWithOwner")
        if not ok:
            return False, [], err or "Connection failed"

        ok, stdout, err = run_gh(
            "issue",
            "list",
            "--repo",
            repo_slug,
            "--label",
            "agent-ready",
            "--state",
            "open",
            "--limit",
            "100",
            "--json",
            "number,title,body",
        )
        if not ok:
            return False, [], err or "Failed to list issues"

        try:
            payload = json.loads(stdout or "[]")
        except json.JSONDecodeError as exc:
            return False, [], f"Invalid gh JSON: {exc}"

        tickets: list[IssueTicket] = []
        for item in payload:
            number = item.get("number")
            if number is None:
                continue
            tickets.append(
                IssueTicket(
                    id=int(number),
                    name=item.get("title") or f"Issue #{number}",
                    body=item.get("body") or "",
                    url="",
                )
            )
        return True, tickets, None

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
            elif kind == "ticket":
                self._ensure_ticket_row(msg[1])

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
                width=-150,
                height=22,
                tag=self._tag(f"ticket_btn_{ticket.id}"),
                user_data=ticket.id,
                callback=self._on_ticket_pressed,
            )
            dpg.bind_item_theme(btn, self._tag("ticket_theme"))
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

        with self._url_lock:
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

        fields = {
            "repo": repo_name,
            "URL": repo_url,
            "new_wt": "true",
            "wt": "",
            "ticket_name": ticket.name,
            "instructions": ticket.body or ticket.name,
            "model": model,
        }

        if self._redis is None:
            self._connect_redis()

        status = self._tag("status_text")
        if self._redis is None:
            if dpg.does_item_exist(status):
                dpg.set_value(status, "Redis unavailable — could not dispatch")
                dpg.configure_item(status, color=COLOR_RED)
            return

        try:
            entry_id = self._redis.xadd(REDIS_STREAM_KEY, fields)
            self._dispatched.add(issue_id)
            if dpg.does_item_exist(status):
                dpg.set_value(
                    status,
                    f"Dispatched #{issue_id} → Redis stream {REDIS_STREAM_KEY} ({entry_id})",
                )
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
) -> None:
    """Module-level builder for FeSpec / MegaDesk canvas hosting."""
    TicketDispatcher().build_ui(
        parent,
        tag_prefix=tag_prefix,
        width=width,
        height=height,
    )


def main() -> None:
    raise SystemExit(
        "Ticket Dispatcher FE is canvas-only. Drop it from the MegaDesk Catalog."
    )


if __name__ == "__main__":
    main()
