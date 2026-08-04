"""Ticket Dispatcher — visualize agent-ready git issues and dispatch them via Redis."""

from __future__ import annotations

import json
import queue
import re
import subprocess
import threading
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

import dearpygui.dearpygui as dpg
import redis

POLL_INTERVAL_SEC = 3.0
REDIS_HOST = "localhost"
REDIS_PORT = 6379
REDIS_STREAM_KEY = "WORKORDER"
DEFAULT_MODEL = "auto"
GH_TIMEOUT_SEC = 15

COLOR_GREEN = (80, 200, 80, 255)
COLOR_RED = (220, 70, 70, 255)
COLOR_BLUE = (70, 140, 230, 255)
COLOR_DIM = (90, 90, 90, 255)


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

    # git@github.com:owner/repo.git
    ssh = re.match(r"git@github\.com:([^/]+)/([^/]+?)(?:\.git)?/?$", url)
    if ssh:
        return ssh.group(1), ssh.group(2)

    # https://github.com/owner/repo[.git]
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
    """Canonical https URL for the repo (used in Redis payload)."""
    return f"https://github.com/{owner}/{repo}"


def run_gh(*args: str) -> tuple[bool, str, str]:
    """Run a `gh` subcommand. Returns (ok, stdout, error_message)."""
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

        self._connect_redis()

    def _connect_redis(self) -> None:
        try:
            self._redis = redis.Redis(
                host=REDIS_HOST, port=REDIS_PORT, decode_responses=True
            )
            self._redis.ping()
        except redis.RedisError:
            self._redis = None

    def start(self) -> None:
        dpg.create_context()
        self._build_ui()
        dpg.create_viewport(title="Ticket Dispatcher", width=720, height=560)
        dpg.setup_dearpygui()
        dpg.show_viewport()
        dpg.set_primary_window("primary", True)

        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

        while dpg.is_dearpygui_running():
            self._sync_url_from_input()
            self._drain_ui_queue()
            dpg.render_dearpygui_frame()

        self._stop.set()
        if self._poll_thread:
            self._poll_thread.join(timeout=2.0)
        dpg.destroy_context()

    def _build_ui(self) -> None:
        with dpg.theme(tag="ticket_theme"):
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button, (45, 45, 50, 255))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (60, 60, 70, 255))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (55, 55, 65, 255))
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 4)
                dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 10, 8)

        with dpg.window(tag="primary", label="Ticket Dispatcher"):
            dpg.add_text("Ticket Dispatcher")
            dpg.add_spacer(height=6)

            with dpg.group(horizontal=True):
                dpg.add_text("Git URL")
                dpg.add_input_text(
                    tag="git_url",
                    width=480,
                    hint="https://github.com/owner/repo",
                    callback=self._on_url_changed,
                    on_enter=True,
                )
                with dpg.drawlist(width=22, height=22, tag="conn_light_dl"):
                    dpg.draw_circle(
                        (11, 11),
                        8,
                        fill=COLOR_DIM,
                        color=COLOR_DIM,
                        tag="conn_light",
                    )

            dpg.add_spacer(height=4)
            with dpg.group(horizontal=True):
                dpg.add_text("Model")
                dpg.add_input_text(
                    tag="model",
                    width=480,
                    default_value=DEFAULT_MODEL,
                    hint="model id",
                )

            dpg.add_spacer(height=4)
            dpg.add_text("Idle", tag="status_text", color=(160, 160, 160))
            dpg.add_separator()
            dpg.add_text("Agent-ready tickets")
            dpg.add_child_window(
                tag="ticket_scroll",
                width=-1,
                height=-1,
                border=True,
            )

    def _set_conn_light(self, color: tuple[int, int, int, int]) -> None:
        if dpg.does_item_exist("conn_light"):
            dpg.configure_item("conn_light", fill=color, color=color)

    def _sync_url_from_input(self) -> None:
        if not dpg.does_item_exist("git_url"):
            return
        with self._url_lock:
            self._current_repo_url = dpg.get_value("git_url").strip()

    def _on_url_changed(self, sender, app_data, user_data=None) -> None:
        self._sync_url_from_input()
        self._ui_queue.put(("status", "Checking remote…", (180, 180, 100)))

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            with self._url_lock:
                url = self._current_repo_url

            if not url:
                self._ui_queue.put(("conn", False))
                self._ui_queue.put(("status", "Enter a GitHub repository URL", (160, 160, 160)))
                self._stop.wait(POLL_INTERVAL_SEC)
                continue

            parsed = parse_github_repo(url)
            if not parsed:
                self._ui_queue.put(("conn", False))
                self._ui_queue.put(
                    ("status", "Unsupported URL (GitHub https or SSH required)", COLOR_RED)
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
        """Check repo reachability and list open issues labeled agent-ready via gh."""
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
                if dpg.does_item_exist("status_text"):
                    dpg.set_value("status_text", text)
                    dpg.configure_item("status_text", color=color)
            elif kind == "ticket":
                self._ensure_ticket_row(msg[1])

    def _ensure_ticket_row(self, ticket: IssueTicket) -> None:
        if ticket.id in self._tickets:
            # Refresh stored content in case the issue body changed.
            self._tickets[ticket.id] = ticket
            return

        self._tickets[ticket.id] = ticket
        light_tag = f"ticket_light_{ticket.id}"
        row_tag = f"ticket_row_{ticket.id}"

        with dpg.group(parent="ticket_scroll", horizontal=True, tag=row_tag):
            with dpg.drawlist(width=22, height=22):
                dpg.draw_circle(
                    (11, 11),
                    8,
                    fill=COLOR_GREEN,
                    color=COLOR_GREEN,
                    tag=light_tag,
                )
            btn = dpg.add_button(
                label=ticket.name,
                width=-1,
                height=28,
                user_data=ticket.id,
                callback=self._on_ticket_pressed,
            )
            dpg.bind_item_theme(btn, "ticket_theme")

        dpg.add_spacer(parent="ticket_scroll", height=4)

    def _on_ticket_pressed(self, sender, app_data, user_data: int) -> None:
        issue_id = user_data
        ticket = self._tickets.get(issue_id)
        if ticket is None:
            return

        light_tag = f"ticket_light_{issue_id}"
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
        if dpg.does_item_exist("model"):
            model = (dpg.get_value("model") or "").strip() or DEFAULT_MODEL

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

        if self._redis is None:
            dpg.set_value("status_text", "Redis unavailable — could not dispatch")
            dpg.configure_item("status_text", color=COLOR_RED)
            return

        try:
            entry_id = self._redis.xadd(REDIS_STREAM_KEY, fields)
            self._dispatched.add(issue_id)
            dpg.set_value(
                "status_text",
                f"Dispatched #{issue_id} → Redis stream {REDIS_STREAM_KEY} ({entry_id})",
            )
            dpg.configure_item("status_text", color=COLOR_BLUE)
        except redis.RedisError as exc:
            dpg.set_value("status_text", f"Redis xadd failed: {exc}")
            dpg.configure_item("status_text", color=COLOR_RED)
            self._redis = None


def main() -> None:
    TicketDispatcher().start()


if __name__ == "__main__":
    main()
