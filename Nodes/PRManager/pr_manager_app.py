"""PRManager — show and open PRs from GitHub issues labeled merge_success."""

from __future__ import annotations

import queue
import re
import threading
import webbrowser
from dataclasses import dataclass, field
from typing import Mapping, Optional

import dearpygui.dearpygui as dpg
from megadesk_contracts import coerce_parameters, frame_pump, list_github_issues
from megadesk_contracts.github import parse_github_repo, resolve_github_remote, run_gh

POLL_INTERVAL_SEC = 3.0
MERGE_SUCCESS_LABEL = "MERGE_SUCCESS"
PARAM_GIT_URL = "GIT_URL"

COLOR_OK = (80, 200, 80, 255)
COLOR_ERR = (220, 70, 70, 255)
COLOR_DIM = (140, 140, 140, 255)
COLOR_INFO = (70, 140, 230, 255)

_PR_URL_RE = re.compile(
    r"https://github\.com/[^/\s]+/[^/\s]+/pull/\d+",
    re.IGNORECASE,
)
_MARKER_RE = re.compile(r"megadesk:(?:merge-check|merge_success):pr-(\d+)")

# Keep live instances alive while their embed windows exist.
_LIVE: dict[str, "PRManager"] = {}


@dataclass
class MergeIssue:
    id: int
    name: str
    body: str
    pr_url: str
    row_tag: str = field(default="")


def pr_url_from_issue(body: str, owner: str, repo: str) -> str:
    """Pull the tracked PR URL out of a merge_success issue body.

    merge-check writes the PR link into the body, plus a
    ``<!-- megadesk:merge-check:pr-N -->`` marker as a fallback.
    """
    text = body or ""
    match = _PR_URL_RE.search(text)
    if match:
        return match.group(0)
    marker = _MARKER_RE.search(text)
    if marker:
        return f"https://github.com/{owner}/{repo}/pull/{marker.group(1)}"
    return ""


def short_pr_url(url: str) -> str:
    text = (url or "").strip()
    if not text:
        return ""
    if len(text) <= 48:
        return text
    return "…" + text[-47:]


def open_pr_url(url: str) -> tuple[bool, str]:
    text = (url or "").strip()
    if not text:
        return False, "No PR URL"
    try:
        opened = webbrowser.open(text)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    if not opened:
        return False, f"Could not open {short_pr_url(text)}"
    return True, f"Opened {short_pr_url(text)}"


class PRManager:
    def __init__(self, parameters: Optional[Mapping[str, str]] = None) -> None:
        self._ui_queue: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._poll_thread: Optional[threading.Thread] = None
        self._url_lock = threading.Lock()
        values = coerce_parameters(parameters)
        self._current_repo_url = values.get(PARAM_GIT_URL, "").strip()
        self._items: dict[int, MergeIssue] = {}
        self._root_tag = "primary"
        self._frame_registered = False
        self._row_h = 26
        self._scroll_max: Optional[int] = None

    def _tag(self, suffix: str) -> str:
        return f"{self._root_tag}::{suffix}"

    def _set_status(self, text: str, color: tuple[int, int, int, int] = COLOR_DIM) -> None:
        tag = self._tag("status_text")
        if dpg.does_item_exist(tag):
            dpg.set_value(tag, text)
            dpg.configure_item(tag, color=color)

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

            resolved, status = resolve_github_remote(url)
            if resolved is None:
                self._ui_queue.put(("conn", False))
                self._ui_queue.put(
                    (
                        "status",
                        status or "Enter a GitHub repository URL",
                        COLOR_ERR if url else (160, 160, 160),
                    )
                )
                self._stop.wait(POLL_INTERVAL_SEC)
                continue

            owner, repo, _repo_url = resolved
            ok, issues, err = self._fetch_merge_success(owner, repo)
            if not ok:
                self._ui_queue.put(("conn", False))
                self._ui_queue.put(("status", err or "Connection failed", COLOR_ERR))
            else:
                self._ui_queue.put(("conn", True))
                self._ui_queue.put(
                    (
                        "status",
                        f"Connected — {len(issues)} merge_success issue(s)",
                        COLOR_OK,
                    )
                )
                for issue in issues:
                    self._ui_queue.put(("issue", issue))

            self._stop.wait(POLL_INTERVAL_SEC)

    def _fetch_merge_success(
        self, owner: str, repo: str
    ) -> tuple[bool, list[MergeIssue], Optional[str]]:
        ok, items, err = list_github_issues(
            owner, repo, MERGE_SUCCESS_LABEL, gh=run_gh
        )
        if not ok:
            return False, [], err
        issues = [
            MergeIssue(
                id=item["number"],
                name=item["title"],
                body=item["body"],
                pr_url=pr_url_from_issue(item["body"], owner, repo),
            )
            for item in items
        ]
        return True, issues, None

    def _drain_ui_queue(self) -> None:
        while True:
            try:
                msg = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            kind = msg[0]
            if kind == "conn":
                self._set_conn_light(COLOR_OK if msg[1] else COLOR_ERR)
            elif kind == "status":
                self._set_status(msg[1], msg[2])
            elif kind == "issue":
                self._add_row(msg[1])

    def _row_label(self, item: MergeIssue) -> str:
        bits = [item.name]
        short = short_pr_url(item.pr_url)
        if short:
            bits.append(short)
        return "  —  ".join(bits)

    def _resize_issue_scroll(self) -> None:
        scroll = self._tag("issue_scroll")
        if not dpg.does_item_exist(scroll):
            return
        n = max(2, len(self._items))
        h = n * self._row_h
        if self._scroll_max is not None:
            h = min(h, self._scroll_max)
        dpg.configure_item(scroll, height=h)

    def _add_row(self, issue: MergeIssue) -> None:
        if issue.id in self._items:
            issue.row_tag = self._items[issue.id].row_tag
            self._items[issue.id] = issue
            name_tag = self._tag(f"name::{issue.id}")
            open_tag = self._tag(f"open_pr::{issue.id}")
            if dpg.does_item_exist(name_tag):
                dpg.set_value(name_tag, self._row_label(issue))
            if dpg.does_item_exist(open_tag):
                dpg.configure_item(open_tag, enabled=bool(issue.pr_url))
            return

        scroll = self._tag("issue_scroll")
        if not dpg.does_item_exist(scroll):
            return

        row_tag = self._tag(f"row::{issue.id}")
        issue.row_tag = row_tag
        self._items[issue.id] = issue
        with dpg.group(parent=scroll, horizontal=True, tag=row_tag):
            dpg.add_button(
                label="open PR",
                width=70,
                tag=self._tag(f"open_pr::{issue.id}"),
                callback=self._on_open_pr,
                user_data=issue.id,
                enabled=bool(issue.pr_url),
            )
            dpg.add_text(self._row_label(issue), tag=self._tag(f"name::{issue.id}"))
            dpg.add_button(
                label="dismiss",
                width=60,
                tag=self._tag(f"dismiss::{issue.id}"),
                callback=self._on_dismiss_row,
                user_data=issue.id,
            )
        self._resize_issue_scroll()

    def _on_open_pr(self, _sender: str, _app_data: object, issue_id: int) -> None:
        item = self._items.get(issue_id)
        if item is None:
            return
        ok, msg = open_pr_url(item.pr_url)
        self._set_status(msg, COLOR_OK if ok else COLOR_ERR)

    def _close_issue(self, issue_id: int) -> tuple[bool, str]:
        with self._url_lock:
            url = self._current_repo_url
        parsed = parse_github_repo(url)
        if not parsed:
            return False, "Unsupported URL (GitHub https or SSH required)"
        owner, repo = parsed
        ok, _, err = run_gh(
            "issue", "close", str(issue_id), "--repo", f"{owner}/{repo}"
        )
        if not ok:
            return False, err or f"Failed to close #{issue_id}"
        return True, f"Dismissed #{issue_id}"

    def _on_dismiss_row(self, _sender: str, _app_data: object, issue_id: int) -> None:
        item = self._items.get(issue_id)
        name = item.name if item else str(issue_id)
        ok, msg = self._close_issue(issue_id)
        if not ok:
            self._set_status(msg, COLOR_ERR)
            return
        dropped = self._items.pop(issue_id, item)
        if dropped is not None and dropped.row_tag and dpg.does_item_exist(dropped.row_tag):
            dpg.delete_item(dropped.row_tag)
        self._resize_issue_scroll()
        self._set_status(f"Dismissed {name}", COLOR_OK)

    def build_ui(
        self,
        parent: str,
        *,
        tag_prefix: str,
        width: int = 480,
        height: int = 160,
    ) -> None:
        """Fill the host content parent with PRManager widgets."""
        self._root_tag = tag_prefix
        _ = width
        self._scroll_max = max(self._row_h * 2, height - 48) if height else None

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

            dpg.add_text(
                "Idle", tag=self._tag("status_text"), color=(160, 160, 160)
            )
            dpg.add_child_window(
                tag=self._tag("issue_scroll"),
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
        self._poll_thread = threading.Thread(
            target=self._poll_loop, name="merge-success-poll", daemon=True
        )
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
        if self._poll_thread is not None:
            self._poll_thread.join(timeout=2.0)
            self._poll_thread = None
        _LIVE.pop(self._root_tag, None)


def build_ui(
    parent: str,
    *,
    tag_prefix: str,
    width: int = 480,
    height: int = 160,
    parameters: Optional[Mapping[str, str]] = None,
) -> None:
    """Module-level builder for FeSpec / MegaDesk canvas hosting."""
    PRManager(parameters).build_ui(
        parent,
        tag_prefix=tag_prefix,
        width=width,
        height=height,
    )


def read_parameters(tag_prefix: str) -> dict[str, str]:
    """Current parameter values of the instance hosted under ``tag_prefix``."""
    tag = f"{tag_prefix}::git_url"
    if not dpg.does_item_exist(tag):
        return {}
    return {PARAM_GIT_URL: (dpg.get_value(tag) or "").strip()}


def main() -> None:
    raise SystemExit(
        "PRManager FE is canvas-only. Drop it from the MegaDesk Catalog."
    )


if __name__ == "__main__":
    main()
