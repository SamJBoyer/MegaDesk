"""Auto Integrate — a human gate that sends an agent at a pull request that stopped merging.

The gate lists open PRs whose merge-check (``mergeable``) failed.
Pressing a row orders a factory to fix that PR *on its own branch*, which is
the whole reason the order carries a ``ref``: starting from ``dev`` would
produce a fix that never reaches the branch it is meant to unblock. The
clicked PR is then stale on the bar so a later poll cannot dispatch it twice.
"""

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
    MERGE_CHECK_FAILURE,
    check_repo,
    list_merge_prs,
    normalize_repo_url,
    parse_github_repo,
    run_gh,
)
from megadesk_contracts.wire.cloud import (
    CLOUDORDER_CHANNEL,
    cloudorder_fields,
    new_order_id,
    publish_cloudorder,
)
from megadesk_contracts.wire.factory import DEFAULT_STARTING_REF
from megadesk_contracts.wire.machine import (
    DEFAULT_MODEL,
    WORKORDER_CHANNEL,
    publish_workorder,
    workorder_fields,
)

POLL_INTERVAL_SEC = 3.0
MODEL_OPTIONS = ("auto", "grok-4.6", "claude-opus-5")
FACTORY_OPTIONS = ("machine", "cloud")
DEFAULT_FACTORY = "machine"

# Graph parameters this node recognizes; declared in parameters.yaml.
PARAM_GIT_URL = "GIT_URL"
PARAM_MAX_DEPTH = "MAX_DEPTH"

HEADER_H = 48
ROW_H = 26
DEFAULT_DEPTH = 2
DEFAULT_WIDTH = 520
DEFAULT_HEIGHT = HEADER_H + DEFAULT_DEPTH * ROW_H + 10
MAX_DEPTH_LIMIT = 20

COLOR_GREEN = (80, 200, 80, 255)
COLOR_RED = (220, 70, 70, 255)
COLOR_BLUE = (70, 140, 230, 255)
COLOR_DIM = (90, 90, 90, 255)

FIX_INSTRUCTIONS = """Pull request #{pr} on branch `{branch}` no longer merges into `{base}`.

Work on `{branch}`: bring `{base}` into it and resolve every conflict so the \
pull request is mergeable again. Keep the branch's own changes intact and \
change nothing the conflict does not require.

{detail}"""


def _clamp_depth(value: object) -> int:
    try:
        depth = int(value)
    except (TypeError, ValueError):
        return DEFAULT_DEPTH
    return max(1, min(MAX_DEPTH_LIMIT, depth))


# Keep live instances alive while their embed windows exist.
_LIVE: dict[str, "AutoIntegrate"] = {}


@dataclass
class GateRow:
    id: int
    name: str
    url: str
    branch: str
    base: str


class AutoIntegrate:
    def __init__(self, parameters: Optional[Mapping[str, str]] = None) -> None:
        self._ui_queue: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._poll_thread: Optional[threading.Thread] = None
        self._state_lock = threading.Lock()
        values = coerce_parameters(parameters)
        self._current_repo_url = values.get(PARAM_GIT_URL, "").strip()
        self._max_depth = _clamp_depth(values.get(PARAM_MAX_DEPTH, "") or DEFAULT_DEPTH)
        self._rows: dict[int, GateRow] = {}
        self._stale: set[int] = set()
        self._redis: Optional[redis.Redis] = None
        self.redis_url = resolve_redis_url()
        self._root_tag = "primary"
        self._content_parent = ""
        self._frame_registered = False
        self._row_h = ROW_H
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
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
    ) -> None:
        """Fill the host content parent with Auto Integrate widgets."""
        self._root_tag = tag_prefix
        self._content_parent = parent
        _ = width, height

        theme_tag = self._tag("gate_theme")
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
                    width=-80,
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
                dpg.add_input_int(
                    tag=self._tag("max_depth"),
                    default_value=self._max_depth,
                    width=48,
                    min_value=1,
                    max_value=MAX_DEPTH_LIMIT,
                    min_clamped=True,
                    max_clamped=True,
                    callback=self._on_depth_changed,
                )

            dpg.add_text(
                "Idle", tag=self._tag("status_text"), color=(160, 160, 160)
            )
            dpg.add_child_window(
                tag=self._tag("issue_scroll"),
                width=-1,
                height=self._list_height(),
                border=True,
            )

        self._apply_layout()
        dpg.set_item_user_data(parent, self.shutdown)
        self._start_services()
        _LIVE[tag_prefix] = self

    def _start_services(self) -> None:
        if self._poll_thread and self._poll_thread.is_alive():
            return
        self._stop.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop, name="auto-integrate-poll", daemon=True
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
        if self._poll_thread:
            self._poll_thread.join(timeout=2.0)
            self._poll_thread = None
        _LIVE.pop(self._root_tag, None)

    # --- input ---

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

    def _drop_row(self, rid: int) -> None:
        tag = self._tag(f"issue_row_{rid}")
        if dpg.does_item_exist(tag):
            dpg.delete_item(tag)
        self._rows.pop(rid, None)

    def _sync_rows(self, rows: list[GateRow]) -> None:
        live_ids = {row.id for row in rows}
        self._stale &= live_ids
        visible = [row for row in rows if row.id not in self._stale]
        seen = {row.id for row in visible}
        for rid in list(self._rows):
            if rid not in seen:
                self._drop_row(rid)
        for row in visible:
            self._ensure_row(row)

    # --- polling ---

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            with self._state_lock:
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
            ok, rows, err = self._fetch_rows(owner, repo)
            if not ok:
                self._ui_queue.put(("conn", False))
                self._ui_queue.put(("status", err or "Connection failed", COLOR_RED))
            else:
                self._ui_queue.put(("conn", True))
                self._ui_queue.put(
                    (
                        "status",
                        f"Connected — {len(rows)} conflicting PR(s)",
                        COLOR_GREEN,
                    )
                )
                self._ui_queue.put(("sync", rows))

            self._stop.wait(POLL_INTERVAL_SEC)

    def _fetch_rows(
        self, owner: str, repo: str
    ) -> tuple[bool, list[GateRow], Optional[str]]:
        ok, err = check_repo(owner, repo, gh=run_gh)
        if not ok:
            return False, [], err

        ok, prs, err = list_merge_prs(
            owner, repo, MERGE_CHECK_FAILURE, gh=run_gh
        )
        if not ok:
            return False, [], err

        rows = [
            GateRow(
                id=pr.number,
                name=pr.title,
                url=pr.url,
                branch=pr.branch,
                base=pr.base,
            )
            for pr in prs
        ]
        return True, rows, None

    # --- rendering ---

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
                self._set_status(msg[1], msg[2])
            elif kind == "sync":
                self._sync_rows(msg[1])

    def _set_status(self, text: str, color: tuple[int, int, int, int]) -> None:
        tag = self._tag("status_text")
        if dpg.does_item_exist(tag):
            dpg.set_value(tag, text)
            dpg.configure_item(tag, color=color)

    def _list_height(self) -> int:
        return self._max_depth * self._row_h

    def _apply_layout(self) -> None:
        list_h = self._list_height()
        scroll = self._tag("issue_scroll")
        if dpg.does_item_exist(scroll):
            dpg.configure_item(scroll, height=list_h)
        content = self._content_parent or f"{self._root_tag}::content"
        if content and dpg.does_item_exist(content):
            dpg.configure_item(content, height=HEADER_H + list_h + 10)

    def _on_depth_changed(self, sender, app_data, user_data=None) -> None:
        depth = _clamp_depth(
            app_data if app_data is not None else dpg.get_value(sender)
        )
        self._max_depth = depth
        if dpg.does_item_exist(sender):
            dpg.set_value(sender, depth)
        self._apply_layout()

    def _row_label(self, row: GateRow) -> str:
        """The branch is what the operator is deciding about, so lead with it."""
        if row.branch:
            return f"#{row.id} {row.branch}"
        return row.name

    def _ensure_row(self, row: GateRow) -> None:
        known = self._rows.get(row.id)
        if known is not None:
            self._rows[row.id] = row
            btn = self._tag(f"issue_btn_{row.id}")
            if dpg.does_item_exist(btn):
                dpg.configure_item(
                    btn, label=self._row_label(row), enabled=bool(row.branch)
                )
            self._set_row_light(row)
            return

        scroll = self._tag("issue_scroll")
        if not dpg.does_item_exist(scroll):
            return

        self._rows[row.id] = row
        row_tag = self._tag(f"issue_row_{row.id}")
        with dpg.group(parent=scroll, horizontal=True, tag=row_tag):
            with dpg.drawlist(width=16, height=16):
                dpg.draw_circle(
                    (8, 8),
                    6,
                    fill=COLOR_DIM,
                    color=COLOR_DIM,
                    tag=self._tag(f"issue_light_{row.id}"),
                )
            btn = dpg.add_button(
                label=self._row_label(row),
                width=-250,
                height=22,
                tag=self._tag(f"issue_btn_{row.id}"),
                user_data=row.id,
                callback=self._on_row_pressed,
                enabled=bool(row.branch),
            )
            dpg.bind_item_theme(btn, self._tag("gate_theme"))
            dpg.add_combo(
                items=list(FACTORY_OPTIONS),
                default_value=DEFAULT_FACTORY,
                width=90,
                height_mode=dpg.mvComboHeight_Small,
                tag=self._tag(f"issue_factory_{row.id}"),
            )
            dpg.add_combo(
                items=list(MODEL_OPTIONS),
                default_value=DEFAULT_MODEL,
                width=140,
                height_mode=dpg.mvComboHeight_Small,
                tag=self._tag(f"issue_model_{row.id}"),
            )
        self._set_row_light(row)

    def _set_row_light(self, row: GateRow) -> None:
        tag = self._tag(f"issue_light_{row.id}")
        if not dpg.does_item_exist(tag):
            return
        color = COLOR_GREEN if row.branch else COLOR_RED
        dpg.configure_item(tag, fill=color, color=color)

    # --- dispatch ---

    def _on_row_pressed(self, sender, app_data, user_data: int) -> None:
        issue_id = user_data
        if issue_id in self._stale:
            return
        row = self._rows.get(issue_id)
        if row is None:
            return

        branch = row.branch
        if not branch:
            self._set_status(f"No PR branch on #{issue_id}", COLOR_RED)
            return
        base = row.base or DEFAULT_STARTING_REF

        with self._state_lock:
            url = self._current_repo_url
        parsed = parse_github_repo(url)
        if not parsed:
            self._set_status(
                "Unsupported URL (GitHub https or SSH required)", COLOR_RED
            )
            return
        owner, repo = parsed
        repo_url = normalize_repo_url(url, owner, repo)

        model = DEFAULT_MODEL
        model_tag = self._tag(f"issue_model_{issue_id}")
        if dpg.does_item_exist(model_tag):
            model = (dpg.get_value(model_tag) or "").strip() or DEFAULT_MODEL

        factory = DEFAULT_FACTORY
        factory_tag = self._tag(f"issue_factory_{issue_id}")
        if dpg.does_item_exist(factory_tag):
            factory = (dpg.get_value(factory_tag) or "").strip() or DEFAULT_FACTORY

        ticket_name = f"merge-fix-pr-{row.id}"
        instructions = FIX_INSTRUCTIONS.format(
            pr=row.id,
            branch=branch,
            base=base,
            detail=(row.name or "").strip(),
        )

        try:
            if factory == "machine":
                channel = WORKORDER_CHANNEL
                fields = workorder_fields(
                    repo=repo,
                    url=repo_url,
                    ref=branch,
                    ticket_name=ticket_name,
                    instructions=instructions,
                    model=model,
                    auto_pr=True,
                )
                publish = publish_workorder
            elif factory == "cloud":
                channel = CLOUDORDER_CHANNEL
                fields = cloudorder_fields(
                    order_id=new_order_id(),
                    repo_url=repo_url,
                    ref=branch,
                    title=ticket_name,
                    instructions=instructions,
                    model=model,
                    auto_pr=True,
                )
                publish = publish_cloudorder
            else:
                raise ValueError(f"unknown factory {factory!r}")
        except ValueError as exc:
            self._set_status(f"Cannot dispatch #{issue_id}: {exc}", COLOR_RED)
            return

        if self._redis is None:
            self._connect_redis()
        if self._redis is None:
            self._set_status("Redis unavailable — could not dispatch", COLOR_RED)
            return

        try:
            publish(self._redis, fields)
        except redis.RedisError as exc:
            self._set_status(f"Redis publish failed: {exc}", COLOR_RED)
            self._redis = None
            return

        self._stale.add(issue_id)
        self._drop_row(issue_id)
        self._resize_scroll()
        self._set_status(f"Dispatched {branch} → {channel}", COLOR_BLUE)


def build_ui(
    parent: str,
    *,
    tag_prefix: str,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    parameters: Optional[Mapping[str, str]] = None,
) -> None:
    """Module-level builder for FeSpec / MegaDesk graph hosting."""
    AutoIntegrate(parameters).build_ui(
        parent,
        tag_prefix=tag_prefix,
        width=width,
        height=height,
    )


def read_parameters(tag_prefix: str) -> dict[str, str]:
    """Current parameter values of the instance hosted under ``tag_prefix``."""
    url_tag = f"{tag_prefix}::git_url"
    if not dpg.does_item_exist(url_tag):
        return {}
    depth_tag = f"{tag_prefix}::max_depth"
    depth = DEFAULT_DEPTH
    if dpg.does_item_exist(depth_tag):
        depth = _clamp_depth(dpg.get_value(depth_tag))
    return {
        PARAM_GIT_URL: (dpg.get_value(url_tag) or "").strip(),
        PARAM_MAX_DEPTH: str(depth),
    }


def main() -> None:
    raise SystemExit(
        "Auto Integrate FE is graph-hosted. Drop it from the MegaDesk Catalog."
    )


if __name__ == "__main__":
    main()
