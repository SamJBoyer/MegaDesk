"""PRManager — show and open PRs whose merge-check status succeeded."""

from __future__ import annotations

import os
import queue
import re
import subprocess
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

import dearpygui.dearpygui as dpg
from megadesk_contracts import coerce_parameters, frame_pump
from megadesk_contracts.human_gate import (
    MERGE_CHECK_SUCCESS,
    check_repo,
    list_merge_prs,
    normalize_repo_url,
    parse_github_repo,
    run_gh,
)
from megadesk_contracts.repo import CloneError, is_clone, safe_repo_name

POLL_INTERVAL_SEC = 3.0
GIT_TIMEOUT_SEC = 300
PARAM_GIT_URL = "GIT_URL"
ENV_SCOPE_ROOT = "PR_SCOPE_ROOT"

COLOR_OK = (80, 200, 80, 255)
COLOR_ERR = (220, 70, 70, 255)
COLOR_DIM = (140, 140, 140, 255)
COLOR_INFO = (70, 140, 230, 255)

# Keep live instances alive while their embed windows exist.
_LIVE: dict[str, "PRManager"] = {}


@dataclass
class MergeIssue:
    id: int
    name: str
    pr_url: str
    row_tag: str = field(default="")
    clone_path: Optional[Path] = None


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


def pr_number_from_url(url: str) -> Optional[int]:
    match = re.search(r"/pull/(\d+)(?:/|$)", (url or "").strip())
    return int(match.group(1)) if match else None


def scope_root() -> Path:
    """Where PR checkouts land. ``PR_SCOPE_ROOT`` overrides the node-local Scope/."""
    configured = (os.environ.get(ENV_SCOPE_ROOT) or "").strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parent / "Scope"


def pr_checkout_path(
    repo: str, pr_number: int, root: Optional[Path] = None
) -> Path:
    return (root if root is not None else scope_root()) / safe_repo_name(repo) / f"pr-{int(pr_number)}"


def _git(args: list[str], *, cwd: Optional[Path] = None) -> str:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SEC,
            check=False,
            env=env,
        )
    except FileNotFoundError as exc:
        raise CloneError("git not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise CloneError(f"git {' '.join(args)} timed out") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise CloneError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout


def pull_pr(
    *,
    url: str,
    repo: str,
    pr_number: int,
    root: Optional[Path] = None,
) -> Path:
    """Clone ``url`` into PRManager's Scope and check out ``refs/pull/<n>/head``.

    Idempotent: an existing checkout is fetched and hard-reset onto the PR head.
    """
    dest = pr_checkout_path(repo, pr_number, root)
    clone_url = (url or "").strip()
    if not clone_url:
        raise ValueError("pull_pr requires a repository URL")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not is_clone(dest):
        if dest.exists() and any(dest.iterdir()):
            raise ValueError(
                f"{dest} already exists and is not a git clone; refusing to overwrite it"
            )
        _git(["clone", clone_url, str(dest)])

    ref = f"pr-{int(pr_number)}"
    _git(
        ["fetch", "origin", f"+refs/pull/{int(pr_number)}/head:refs/heads/{ref}"],
        cwd=dest,
    )
    _git(["checkout", "--force", ref], cwd=dest)
    _git(["reset", "--hard", ref], cwd=dest)
    _git(["clean", "-fd"], cwd=dest)
    return dest


def open_in_editor(editor: str, path: Path) -> tuple[bool, str]:
    """Open a folder in VS Code or Cursor IDE (not the agents window)."""
    if not path.is_dir():
        return False, f"Path does not exist: {path}"

    commands = {
        "vscode": ["code", str(path)],
        "cursor": ["cursor", str(path)],
    }
    cmd = commands.get(editor)
    if not cmd:
        return False, f"Unknown editor: {editor}"

    try:
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=os.name == "nt",
        )
    except FileNotFoundError:
        return False, f"{cmd[0]} CLI not found on PATH"
    except OSError as exc:
        return False, str(exc)
    return True, f"Opened {path} in {editor}"


class PRManager:
    def __init__(self, parameters: Optional[Mapping[str, str]] = None) -> None:
        self._ui_queue: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._poll_thread: Optional[threading.Thread] = None
        self._url_lock = threading.Lock()
        values = coerce_parameters(parameters)
        self._current_repo_url = values.get(PARAM_GIT_URL, "").strip()
        self._items: dict[int, MergeIssue] = {}
        self._dismissed: set[int] = set()
        self._jobs: queue.Queue[tuple[int, Optional[str]]] = queue.Queue()
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
        last_list = 0.0
        while not self._stop.is_set():
            try:
                issue_id, then_open = self._jobs.get(timeout=0.05)
            except queue.Empty:
                issue_id = None
                then_open = None

            if issue_id is not None:
                self._do_pull(issue_id, then_open=then_open)
                last_list = time.monotonic()
                continue

            now = time.monotonic()
            if now - last_list < POLL_INTERVAL_SEC:
                continue
            last_list = now

            with self._url_lock:
                url = self._current_repo_url

            if not url:
                self._ui_queue.put(("conn", False))
                self._ui_queue.put(
                    ("status", "Enter a GitHub repository URL", (160, 160, 160))
                )
                continue

            parsed = parse_github_repo(url)
            if not parsed:
                self._ui_queue.put(("conn", False))
                self._ui_queue.put(
                    (
                        "status",
                        "Unsupported URL (GitHub https or SSH required)",
                        COLOR_ERR,
                    )
                )
                continue

            owner, repo = parsed
            ok, issues, err = self._fetch_merge_success(owner, repo)
            if not ok:
                self._ui_queue.put(("conn", False))
                self._ui_queue.put(("status", err or "Connection failed", COLOR_ERR))
            else:
                self._ui_queue.put(("conn", True))
                self._ui_queue.put(
                    (
                        "status",
                        f"Connected — {len(issues)} mergeable PR(s)",
                        COLOR_OK,
                    )
                )
                self._ui_queue.put(("sync", issues))

    def _fetch_merge_success(
        self, owner: str, repo: str
    ) -> tuple[bool, list[MergeIssue], Optional[str]]:
        ok, err = check_repo(owner, repo, gh=run_gh)
        if not ok:
            return False, [], err

        ok, listed, err = list_merge_prs(
            owner, repo, MERGE_CHECK_SUCCESS, gh=run_gh
        )
        if not ok:
            return False, [], err

        issues = [
            MergeIssue(
                id=pr.number,
                name=pr.title or f"PR #{pr.number}",
                pr_url=pr.url,
            )
            for pr in listed
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
            elif kind == "sync":
                self._sync_rows(msg[1])
            elif kind == "pulled":
                self._on_pulled(msg[1], msg[2], msg[3])

    def _sync_rows(self, issues: list[MergeIssue]) -> None:
        live_ids = {issue.id for issue in issues}
        self._dismissed &= live_ids
        visible = [issue for issue in issues if issue.id not in self._dismissed]
        seen = {issue.id for issue in visible}
        for rid in list(self._items):
            if rid not in seen:
                dropped = self._items.pop(rid)
                if dropped.row_tag and dpg.does_item_exist(dropped.row_tag):
                    dpg.delete_item(dropped.row_tag)
        for issue in visible:
            self._add_row(issue)
        self._resize_issue_scroll()

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

    def _has_pr(self, issue: MergeIssue) -> bool:
        return bool(issue.pr_url) and pr_number_from_url(issue.pr_url) is not None

    def _existing_checkout(self, issue: MergeIssue) -> Optional[Path]:
        number = pr_number_from_url(issue.pr_url)
        if number is None:
            return None
        with self._url_lock:
            url = self._current_repo_url
        parsed = parse_github_repo(url)
        if not parsed:
            return None
        path = pr_checkout_path(parsed[1], number)
        return path if is_clone(path) else None

    def _set_row_enabled(self, issue: MergeIssue) -> None:
        has_pr = self._has_pr(issue)
        for suffix in ("open_pr", "pull", "vscode", "cursor"):
            tag = self._tag(f"{suffix}::{issue.id}")
            if dpg.does_item_exist(tag):
                dpg.configure_item(tag, enabled=has_pr)

    def _add_row(self, issue: MergeIssue) -> None:
        if issue.clone_path is None:
            issue.clone_path = self._existing_checkout(issue)
        if issue.id in self._items:
            previous = self._items[issue.id]
            issue.row_tag = previous.row_tag
            if issue.clone_path is None:
                issue.clone_path = previous.clone_path
            self._items[issue.id] = issue
            name_tag = self._tag(f"name::{issue.id}")
            if dpg.does_item_exist(name_tag):
                dpg.set_value(name_tag, self._row_label(issue))
            self._set_row_enabled(issue)
            return

        scroll = self._tag("issue_scroll")
        if not dpg.does_item_exist(scroll):
            return

        row_tag = self._tag(f"row::{issue.id}")
        issue.row_tag = row_tag
        self._items[issue.id] = issue
        has_pr = self._has_pr(issue)
        with dpg.group(parent=scroll, horizontal=True, tag=row_tag):
            dpg.add_button(
                label="open PR",
                width=70,
                tag=self._tag(f"open_pr::{issue.id}"),
                callback=self._on_open_pr,
                user_data=issue.id,
                enabled=has_pr,
            )
            dpg.add_button(
                label="pull",
                width=40,
                tag=self._tag(f"pull::{issue.id}"),
                callback=self._on_pull,
                user_data=issue.id,
                enabled=has_pr,
            )
            dpg.add_button(
                label="vscode",
                width=55,
                tag=self._tag(f"vscode::{issue.id}"),
                callback=self._on_vscode,
                user_data=issue.id,
                enabled=has_pr,
            )
            dpg.add_button(
                label="cursor",
                width=55,
                tag=self._tag(f"cursor::{issue.id}"),
                callback=self._on_cursor,
                user_data=issue.id,
                enabled=has_pr,
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

    def _on_pull(self, _sender: str, _app_data: object, issue_id: int) -> None:
        if self._items.get(issue_id) is None:
            return
        self._set_status("Pulling PR…", COLOR_INFO)
        self._jobs.put((issue_id, None))

    def _on_vscode(self, _sender: str, _app_data: object, issue_id: int) -> None:
        self._open_or_pull(issue_id, "vscode")

    def _on_cursor(self, _sender: str, _app_data: object, issue_id: int) -> None:
        self._open_or_pull(issue_id, "cursor")

    def _open_or_pull(self, issue_id: int, editor: str) -> None:
        item = self._items.get(issue_id)
        if item is None:
            return
        if item.clone_path is not None and is_clone(item.clone_path):
            ok, msg = open_in_editor(editor, item.clone_path)
            self._set_status(msg, COLOR_OK if ok else COLOR_ERR)
            return
        self._set_status(f"Pulling PR for {editor}…", COLOR_INFO)
        self._jobs.put((issue_id, editor))

    def _do_pull(self, issue_id: int, *, then_open: Optional[str] = None) -> None:
        item = self._items.get(issue_id)
        if item is None:
            return
        pr_number = pr_number_from_url(item.pr_url)
        if pr_number is None:
            self._ui_queue.put(("status", "No PR URL", COLOR_ERR))
            return
        with self._url_lock:
            url = self._current_repo_url
        parsed = parse_github_repo(url)
        if not parsed:
            self._ui_queue.put(
                ("status", "Unsupported URL (GitHub https or SSH required)", COLOR_ERR)
            )
            return
        owner, repo = parsed
        clone_url = normalize_repo_url(url, owner, repo)
        self._ui_queue.put(("status", f"Pulling PR #{pr_number}…", COLOR_INFO))
        try:
            path = pull_pr(url=clone_url, repo=repo, pr_number=pr_number)
        except (CloneError, ValueError, OSError) as exc:
            self._ui_queue.put(("status", f"Pull failed: {exc}", COLOR_ERR))
            return
        self._ui_queue.put(("pulled", issue_id, path, then_open))

    def _on_pulled(
        self, issue_id: int, path: Path, then_open: Optional[str]
    ) -> None:
        item = self._items.get(issue_id)
        if item is not None:
            item.clone_path = path
        self._set_status(f"Pulled {path.name}", COLOR_OK)
        if then_open:
            ok, msg = open_in_editor(then_open, path)
            self._set_status(msg, COLOR_OK if ok else COLOR_ERR)

    def _on_dismiss_row(self, _sender: str, _app_data: object, issue_id: int) -> None:
        item = self._items.get(issue_id)
        name = item.name if item else str(issue_id)
        self._dismissed.add(issue_id)
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
        width: int = 560,
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
    width: int = 560,
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
