"""CloudFactory FE — approve drafts, watch runs, open the pull request.

Two lists, because there are exactly two states a person acts on. A **draft** is
something nobody has agreed to yet, usually written by VoiceDeck, and it does
nothing until pressed; a **run** is already happening on Cursor's machine, and the
only useful things to do with it are watch it and open its PR.

Rows come from the hashes on db 1 rather than from anything held here, so closing
and reopening this panel — or restarting the BE — shows the same truth. The
CLOUDFINISHED stream is read only for the status line, since a finished run has
already updated its own hash.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import webbrowser
from typing import Optional

import dearpygui.dearpygui as dpg
import redis
from megadesk_contracts import REDIS_DB_PERSISTENT, frame_pump, resolve_redis_url
from megadesk_contracts.wire import cloud as wire

log = logging.getLogger("cloud_factory.fe")

POLL_INTERVAL_SEC = 0.5
FINISHED_BATCH = 20
DEFAULT_MODEL = wire.DEFAULT_MODEL
ROW_H = 22

COLOR_GREEN = (80, 200, 80, 255)
COLOR_RED = (220, 70, 70, 255)
COLOR_BLUE = (70, 140, 230, 255)
COLOR_AMBER = (215, 170, 70, 255)
COLOR_DIM = (90, 90, 90, 255)

STATUS_COLORS = {
    wire.STATUS_DRAFT: COLOR_AMBER,
    wire.STATUS_QUEUED: COLOR_AMBER,
    wire.STATUS_RUNNING: COLOR_BLUE,
    wire.STATUS_FINISHED: COLOR_GREEN,
    wire.STATUS_ERROR: COLOR_RED,
    wire.STATUS_STARTUP_ERROR: COLOR_RED,
    wire.STATUS_CANCELLED: COLOR_DIM,
}

# Patchable so a test can assert a link was opened without a browser appearing.
open_url = webbrowser.open

_LIVE: dict[str, "CloudFactoryFE"] = {}


def available_models() -> list[str]:
    """Model ids from Cursor, or none.

    Only asked for when there is a key to ask with: an unauthenticated call would
    cost a network round trip on every canvas boot to learn nothing. Hardcoding
    ids is the alternative, and that is how a combo ends up offering models that
    were retired months ago.
    """
    if not (os.environ.get("CURSOR_API_KEY") or "").strip():
        return []
    try:
        from CloudFactoryManager.runtime import CursorCloudFactory

        return CursorCloudFactory().models()
    except Exception:  # noqa: BLE001 - a model list is cosmetic
        log.debug("Could not list models", exc_info=True)
        return []


class CloudFactoryFE:
    def __init__(self) -> None:
        self.redis_url = resolve_redis_url()
        self._ui_queue: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._cursor = "$"
        self._draft_rows: dict[str, dict[str, str]] = {}
        self._run_rows: dict[str, dict[str, str]] = {}
        self._models_loaded = False
        self._root_tag = "primary"
        self._frame_registered = False
        self._wrap = 460
        self._redis: Optional[redis.Redis] = None
        self._persistent: Optional[redis.Redis] = None
        self._connect_redis()

    # --- plumbing ---

    def _tag(self, suffix: str) -> str:
        return f"{self._root_tag}::{suffix}"

    def _connect_redis(self) -> None:
        try:
            self._redis = redis.Redis.from_url(
                self.redis_url, decode_responses=True, socket_connect_timeout=2
            )
            self._redis.ping()
            self._persistent = redis.Redis.from_url(
                self.redis_url,
                db=REDIS_DB_PERSISTENT,
                decode_responses=True,
                socket_connect_timeout=2,
            )
        except (redis.RedisError, OSError, ValueError):
            self._redis = None
            self._persistent = None

    # --- UI ---

    def build_ui(
        self,
        parent: str,
        *,
        tag_prefix: str,
        width: int = 520,
        height: int = 260,
    ) -> None:
        self._root_tag = tag_prefix
        self._wrap = max(160, width - 96)

        with dpg.group(parent=parent):
            with dpg.group(horizontal=True):
                dpg.add_input_text(
                    tag=self._tag("repo_url"),
                    width=-92,
                    hint="https://github.com/owner/repo",
                )
                dpg.add_combo(
                    items=[DEFAULT_MODEL],
                    default_value=DEFAULT_MODEL,
                    width=74,
                    height_mode=dpg.mvComboHeight_Small,
                    tag=self._tag("model"),
                )
                with dpg.drawlist(width=16, height=16):
                    dpg.draw_circle(
                        (8, 8),
                        6,
                        fill=COLOR_DIM,
                        color=COLOR_DIM,
                        tag=self._tag("conn_light"),
                    )
            with dpg.group(horizontal=True):
                dpg.add_input_text(
                    tag=self._tag("instructions"),
                    width=-64,
                    hint="what to document",
                    callback=self._on_send,
                    on_enter=True,
                )
                dpg.add_button(
                    label="send",
                    width=56,
                    height=22,
                    tag=self._tag("send_btn"),
                    callback=self._on_send,
                )
            dpg.add_text("Idle", tag=self._tag("status_text"), color=COLOR_DIM)
            dpg.add_child_window(
                tag=self._tag("drafts"), width=-1, height=ROW_H * 2, border=True
            )
            dpg.add_child_window(
                tag=self._tag("runs"), width=-1, height=ROW_H * 2, border=True
            )

        dpg.set_item_user_data(parent, self.shutdown)
        self._start_services()
        _LIVE[tag_prefix] = self

    def _start_services(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._stop.clear()
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()
        if not self._frame_registered:
            frame_pump.register(self._on_frame)
            self._frame_registered = True

    def _on_frame(self) -> None:
        if not dpg.does_item_exist(self._root_tag):
            return
        self._drain_ui_queue()

    def shutdown(self) -> None:
        self._stop.set()
        if self._frame_registered:
            frame_pump.unregister(self._on_frame)
            self._frame_registered = False
        if self._worker:
            self._worker.join(timeout=2.0)
            self._worker = None
        # Drafts and runs are deliberately left on db 1: a closed panel is not a
        # cancelled agent, and the PR link has to survive until someone reads it.
        _LIVE.pop(self._root_tag, None)

    # --- callbacks ---

    def _on_send(self, sender=None, app_data=None, user_data=None) -> None:
        """Publish an order typed here, with no draft step.

        Typing the instructions *is* the confirmation. The draft rail exists
        because voice can be misheard, not because dispatch is dangerous.
        """
        repo_url = self._input("repo_url")
        instructions = self._input("instructions")
        if not repo_url:
            self._apply_status("Enter a repository URL", COLOR_AMBER)
            return
        if not instructions:
            self._apply_status("Say what to change", COLOR_AMBER)
            return

        order = wire.cloudorder_fields(
            order_id=wire.new_order_id(),
            repo_url=repo_url,
            title=_title_from(instructions),
            instructions=instructions,
            model=self._input("model") or DEFAULT_MODEL,
            auto_pr=True,
        )
        if not self._publish(order):
            return
        dpg.set_value(self._tag("instructions"), "")
        self._apply_status(f"Queued — {order['title']}", COLOR_BLUE)

    def _on_dispatch_draft(self, sender=None, app_data=None, user_data=None) -> None:
        order_id = str(user_data or "")
        if self._persistent is None or not order_id:
            return
        try:
            stored = self._persistent.hgetall(wire.clouddraft_key(order_id))
        except redis.RedisError as exc:
            self._apply_status(f"Redis read failed: {exc}", COLOR_RED)
            return
        if not stored:
            self._apply_status("That draft is gone", COLOR_AMBER)
            self._remove_draft_row(order_id)
            return

        try:
            # Parsed and rebuilt rather than forwarded: a draft has sat on db 1
            # since some earlier version wrote it, and CLOUDORDER's field set is
            # the one thing the BE is entitled to assume.
            order = wire.cloudorder_fields(**wire.parse_cloudorder(stored))
        except ValueError as exc:
            self._apply_status(f"Unusable draft: {exc}", COLOR_RED)
            return
        if not self._publish(order):
            return

        # Deleted here, not on launch: the row has to stop offering a button the
        # moment it is pressed, or an impatient second click means two PRs.
        try:
            self._persistent.delete(wire.clouddraft_key(order_id))
        except redis.RedisError:
            pass
        self._remove_draft_row(order_id)
        self._apply_status(f"Dispatched — {order['title']}", COLOR_BLUE)

    def _on_discard_draft(self, sender=None, app_data=None, user_data=None) -> None:
        order_id = str(user_data or "")
        if self._persistent is None or not order_id:
            return
        try:
            self._persistent.delete(wire.clouddraft_key(order_id))
        except redis.RedisError:
            pass
        self._remove_draft_row(order_id)
        self._apply_status("Draft discarded", COLOR_DIM)

    def _on_open_pr(self, sender=None, app_data=None, user_data=None) -> None:
        url = str(user_data or "")
        if url:
            open_url(url)

    def _publish(self, order: dict[str, str]) -> bool:
        if self._redis is None:
            self._connect_redis()
        if self._redis is None:
            self._apply_status("Redis unavailable", COLOR_RED)
            return False
        try:
            self._redis.xadd(wire.CLOUDORDER_STREAM, order)
        except redis.RedisError as exc:
            self._apply_status(f"Redis xadd failed: {exc}", COLOR_RED)
            self._redis = None
            return False
        return True

    def _input(self, suffix: str) -> str:
        tag = self._tag(suffix)
        if not dpg.does_item_exist(tag):
            return ""
        return (dpg.get_value(tag) or "").strip()

    # --- worker ---

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            if not self._models_loaded:
                self._models_loaded = True
                models = available_models()
                if models:
                    self._push(("models", models))
            self._scan_hashes()
            self._read_finished()

    def _scan_hashes(self) -> None:
        if self._persistent is None:
            self._connect_redis()
        if self._persistent is None:
            self._push(("conn", False))
            self._stop.wait(POLL_INTERVAL_SEC)
            return
        try:
            drafts = {
                wire.order_id_from_draft_key(key): self._persistent.hgetall(key)
                for key in self._persistent.scan_iter(
                    match=f"{wire.CLOUDDRAFT_PREFIX}*", count=100
                )
            }
            runs = {
                wire.agent_id_from_key(key): self._persistent.hgetall(key)
                for key in self._persistent.scan_iter(
                    match=f"{wire.CLOUDRUN_PREFIX}*", count=100
                )
            }
        except (redis.RedisError, ValueError):
            self._push(("conn", False))
            self._stop.wait(POLL_INTERVAL_SEC)
            return

        self._push(("conn", True))
        self._push(("drafts", drafts))
        self._push(("runs", runs))

    def _read_finished(self) -> None:
        if self._redis is None:
            self._connect_redis()
        if self._redis is None:
            self._stop.wait(POLL_INTERVAL_SEC)
            return
        try:
            batches = self._redis.xread(
                {wire.CLOUDFINISHED_STREAM: self._cursor},
                count=FINISHED_BATCH,
                block=max(50, int(POLL_INTERVAL_SEC * 1000)),
            )
        except redis.RedisError:
            self._redis = None
            self._stop.wait(POLL_INTERVAL_SEC)
            return

        for _stream, messages in batches or []:
            for entry_id, fields in messages:
                self._cursor = entry_id
                try:
                    finished = wire.parse_cloudfinished(fields)
                except ValueError as exc:
                    log.warning("Unusable CLOUDFINISHED %s: %s", entry_id, exc)
                    continue
                self._push(("finished", finished))

    def _push(self, message: tuple) -> None:
        self._ui_queue.put(message)

    # --- UI drain ---

    def _drain_ui_queue(self) -> None:
        while True:
            try:
                message = self._ui_queue.get_nowait()
            except queue.Empty:
                return
            kind = message[0]
            if kind == "conn":
                self._set_light(COLOR_GREEN if message[1] else COLOR_RED)
            elif kind == "models":
                self._apply_models(message[1])
            elif kind == "drafts":
                self._apply_drafts(message[1])
            elif kind == "runs":
                self._apply_runs(message[1])
            elif kind == "finished":
                self._apply_finished(message[1])

    def _apply_models(self, models: list[str]) -> None:
        tag = self._tag("model")
        if not dpg.does_item_exist(tag):
            return
        items = [DEFAULT_MODEL] + [m for m in models if m != DEFAULT_MODEL]
        dpg.configure_item(tag, items=items)

    def _apply_drafts(self, drafts: dict[str, dict[str, str]]) -> None:
        for order_id in list(self._draft_rows):
            if order_id not in drafts:
                self._remove_draft_row(order_id)
        for order_id, raw in drafts.items():
            try:
                draft = wire.parse_cloudorder(raw)
            except ValueError:
                continue
            if order_id in self._draft_rows:
                continue
            self._add_draft_row(order_id, draft)
        self._resize()

    def _add_draft_row(self, order_id: str, draft: dict[str, str]) -> None:
        parent = self._tag("drafts")
        if not dpg.does_item_exist(parent):
            return
        with dpg.group(
            horizontal=True, parent=parent, tag=self._tag(f"draft_row_{order_id}")
        ):
            dpg.add_button(
                label="go",
                width=28,
                height=20,
                tag=self._tag(f"draft_go_{order_id}"),
                user_data=order_id,
                callback=self._on_dispatch_draft,
            )
            dpg.add_button(
                label="x",
                width=20,
                height=20,
                tag=self._tag(f"draft_del_{order_id}"),
                user_data=order_id,
                callback=self._on_discard_draft,
            )
            dpg.add_text(
                draft["title"],
                wrap=self._wrap,
                color=COLOR_AMBER,
                tag=self._tag(f"draft_text_{order_id}"),
            )
        self._draft_rows[order_id] = draft

    def _remove_draft_row(self, order_id: str) -> None:
        self._draft_rows.pop(order_id, None)
        tag = self._tag(f"draft_row_{order_id}")
        if dpg.does_item_exist(tag):
            dpg.delete_item(tag)
        self._resize()

    def _apply_runs(self, runs: dict[str, dict[str, str]]) -> None:
        for agent_id in list(self._run_rows):
            if agent_id not in runs:
                self._remove_run_row(agent_id)
        for agent_id, raw in runs.items():
            try:
                run = wire.parse_cloudrun(raw)
            except ValueError:
                continue
            if agent_id in self._run_rows:
                self._update_run_row(agent_id, run)
            else:
                self._add_run_row(agent_id, run)
        self._resize()

    def _add_run_row(self, agent_id: str, run: dict[str, str]) -> None:
        parent = self._tag("runs")
        if not dpg.does_item_exist(parent):
            return
        with dpg.group(
            horizontal=True, parent=parent, tag=self._tag(f"run_row_{agent_id}")
        ):
            dpg.add_button(
                label="PR",
                width=28,
                height=20,
                show=bool(run["pr_url"]),
                tag=self._tag(f"run_pr_{agent_id}"),
                user_data=run["pr_url"],
                callback=self._on_open_pr,
            )
            dpg.add_text(
                _run_label(run),
                wrap=self._wrap,
                color=STATUS_COLORS.get(run["status"], COLOR_DIM),
                tag=self._tag(f"run_text_{agent_id}"),
            )
        self._run_rows[agent_id] = run

    def _update_run_row(self, agent_id: str, run: dict[str, str]) -> None:
        if self._run_rows.get(agent_id) == run:
            return
        self._run_rows[agent_id] = run
        text = self._tag(f"run_text_{agent_id}")
        if dpg.does_item_exist(text):
            dpg.set_value(text, _run_label(run))
            dpg.configure_item(
                text, color=STATUS_COLORS.get(run["status"], COLOR_DIM)
            )
        button = self._tag(f"run_pr_{agent_id}")
        if dpg.does_item_exist(button):
            dpg.configure_item(
                button, show=bool(run["pr_url"]), user_data=run["pr_url"]
            )

    def _remove_run_row(self, agent_id: str) -> None:
        self._run_rows.pop(agent_id, None)
        tag = self._tag(f"run_row_{agent_id}")
        if dpg.does_item_exist(tag):
            dpg.delete_item(tag)

    def _apply_finished(self, finished: dict[str, str]) -> None:
        status = finished["status"]
        color = STATUS_COLORS.get(status, COLOR_DIM)
        if status == wire.STATUS_FINISHED and finished["pr_url"]:
            self._apply_status(f"PR ready — {finished['pr_url']}", color)
        elif status == wire.STATUS_STARTUP_ERROR:
            self._apply_status("Agent never started — check CURSOR_API_KEY", color)
        else:
            self._apply_status(f"Run {status}", color)

    # --- widgets ---

    def _apply_status(self, text: str, color: tuple[int, int, int, int]) -> None:
        tag = self._tag("status_text")
        if dpg.does_item_exist(tag):
            dpg.set_value(tag, text)
            dpg.configure_item(tag, color=color)

    def _set_light(self, color: tuple[int, int, int, int]) -> None:
        tag = self._tag("conn_light")
        if dpg.does_item_exist(tag):
            dpg.configure_item(tag, fill=color, color=color)

    def _resize(self) -> None:
        for suffix, count in (
            ("drafts", len(self._draft_rows)),
            ("runs", len(self._run_rows)),
        ):
            tag = self._tag(suffix)
            if dpg.does_item_exist(tag):
                dpg.configure_item(tag, height=max(2, count) * ROW_H)


def _run_label(run: dict[str, str]) -> str:
    return f"{run['status']} · {run['title']}"


def _title_from(instructions: str) -> str:
    words = instructions.split()
    return " ".join(words[:8]) or "documentation change"


def build_ui(
    parent: str,
    *,
    tag_prefix: str,
    width: int = 520,
    height: int = 260,
) -> None:
    """Module-level builder for FeSpec / MegaDesk canvas hosting."""
    CloudFactoryFE().build_ui(
        parent, tag_prefix=tag_prefix, width=width, height=height
    )


def main() -> None:
    raise SystemExit(
        "CloudFactory FE is canvas-only. Drop it from the MegaDesk Catalog."
    )


if __name__ == "__main__":
    main()
