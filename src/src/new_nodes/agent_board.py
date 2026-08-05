"""Complex standalone GUI: agent-run observer with filters, table, and log.

Usage:
  python -m new_nodes.agent_board
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal, Optional

import dearpygui.dearpygui as dpg

WIN_W, WIN_H = 780, 520
TAG = "agent_board"
LABEL = "Agent Board"

Status = Literal["queued", "running", "done", "failed"]

_STATES: dict[str, "BoardState"] = {}


@dataclass
class Step:
    id: str
    name: str
    status: Status
    duration_ms: int
    detail: str = ""


@dataclass
class BoardState:
    run_id: str = "run-1042"
    agent: str = "ideation-bot"
    steps: list[Step] = field(default_factory=list)
    log_lines: list[str] = field(default_factory=list)
    filter_status: str = "all"
    selected_step: str | None = None
    tick: int = 0


def _demo_state() -> BoardState:
    return BoardState(
        steps=[
            Step("s1", "Load canvas group", "done", 120, "12 members"),
            Step("s2", "Cluster by proximity", "done", 340, "3 clusters"),
            Step("s3", "Draft PDR outline", "running", 0, "streaming…"),
            Step("s4", "Open ticket draft", "queued", 0, ""),
            Step("s5", "Attach metadata pack", "queued", 0, ""),
        ],
        log_lines=[
            "[00:00.12] run-1042 started (ideation-bot)",
            "[00:00.24] loaded group “Export pipeline”",
            "[00:00.58] cluster A: sticky×4, container×1",
            "[00:01.10] drafting section: Summary",
        ],
    )


def build_ui(
    tag: str = TAG,
    *,
    pos: Optional[tuple[float, float]] = None,
    on_close: Optional[Callable[[], None]] = None,
    no_move: bool = False,
    no_resize: bool = True,
    min_size: tuple[int, int] = (520, 360),
) -> str:
    """Build a rectangular multi-pane agent board. Returns the window tag."""
    if dpg.does_item_exist(tag):
        dpg.delete_item(tag)
    _STATES[tag] = _demo_state()
    state = _STATES[tag]

    kwargs: dict = {}
    if pos is not None:
        kwargs["pos"] = list(pos)

    def _closed() -> None:
        _STATES.pop(tag, None)
        if on_close:
            on_close()

    with dpg.window(
        label=LABEL,
        tag=tag,
        width=WIN_W,
        height=WIN_H,
        no_resize=no_resize,
        min_size=list(min_size),
        no_collapse=True,
        no_move=no_move,
        no_close=on_close is None,
        on_close=lambda: _closed(),
        **kwargs,
    ):
        with dpg.group(horizontal=True):
            dpg.add_text("Run", color=(90, 95, 110, 255))
            dpg.add_input_text(
                tag=f"{tag}::run_id",
                default_value=state.run_id,
                width=110,
                readonly=True,
            )
            dpg.add_text("Agent", color=(90, 95, 110, 255))
            dpg.add_input_text(
                tag=f"{tag}::agent",
                default_value=state.agent,
                width=140,
            )
            dpg.add_spacer(width=12)
            dpg.add_progress_bar(
                tag=f"{tag}::progress",
                default_value=_progress(tag),
                width=180,
                overlay="progress",
            )
            dpg.add_button(label="Advance", callback=lambda: _advance_step(tag))
            dpg.add_button(label="Fail step", callback=lambda: _fail_selected(tag))
            dpg.add_button(label="Reset demo", callback=lambda: _reset_demo(tag))

        dpg.add_separator()

        with dpg.group(horizontal=True):
            with dpg.child_window(width=460, height=-1, border=True):
                with dpg.group(horizontal=True):
                    dpg.add_text("Steps")
                    dpg.add_combo(
                        items=["all", "queued", "running", "done", "failed"],
                        tag=f"{tag}::filter",
                        default_value="all",
                        width=110,
                        callback=lambda s, a, u: _on_filter(tag, a),
                    )
                    dpg.add_text("", tag=f"{tag}::counts", color=(100, 110, 120, 255))

                with dpg.table(
                    tag=f"{tag}::table",
                    header_row=True,
                    borders_innerH=True,
                    borders_outerH=True,
                    borders_innerV=True,
                    borders_outerV=True,
                    row_background=True,
                    resizable=False,
                    policy=dpg.mvTable_SizingFixedFit,
                    scrollY=True,
                    height=-1,
                ):
                    dpg.add_table_column(label="ID", width_fixed=True, init_width_or_weight=40)
                    dpg.add_table_column(label="Step", width_stretch=True, init_width_or_weight=1.0)
                    dpg.add_table_column(label="Status", width_fixed=True, init_width_or_weight=70)
                    dpg.add_table_column(label="ms", width_fixed=True, init_width_or_weight=50)

            with dpg.child_window(width=-1, height=-1, border=True):
                with dpg.tab_bar(tag=f"{tag}::tabs"):
                    with dpg.tab(label="Detail"):
                        dpg.add_text("Selected step", color=(90, 95, 110, 255))
                        dpg.add_text("(none)", tag=f"{tag}::detail_title")
                        dpg.add_separator()
                        dpg.add_input_text(
                            tag=f"{tag}::detail_body",
                            multiline=True,
                            readonly=True,
                            height=120,
                            width=-1,
                            default_value="Select a row to inspect.",
                        )
                        dpg.add_separator()
                        dpg.add_text("Notes")
                        dpg.add_input_text(
                            tag=f"{tag}::notes",
                            multiline=True,
                            height=80,
                            width=-1,
                            hint="Operator notes for this run…",
                        )
                    with dpg.tab(label="Log"):
                        dpg.add_input_text(
                            tag=f"{tag}::log",
                            multiline=True,
                            readonly=True,
                            height=-36,
                            width=-1,
                            default_value="",
                        )
                        with dpg.group(horizontal=True):
                            dpg.add_button(
                                label="Append tick",
                                callback=lambda: _append_log_tick(tag),
                            )
                            dpg.add_button(
                                label="Clear log",
                                callback=lambda: _clear_log(tag),
                            )

    _rebuild_table(tag)
    _refresh_log(tag)
    _refresh_progress(tag)
    return tag


def _state(tag: str) -> BoardState:
    if tag not in _STATES:
        _STATES[tag] = _demo_state()
    return _STATES[tag]


def _progress(tag: str) -> float:
    st = _state(tag)
    if not st.steps:
        return 0.0
    done = sum(1 for s in st.steps if s.status in ("done", "failed"))
    return done / len(st.steps)


def _refresh_progress(tag: str) -> None:
    st = _state(tag)
    val = _progress(tag)
    done = sum(1 for s in st.steps if s.status == "done")
    failed = sum(1 for s in st.steps if s.status == "failed")
    running = sum(1 for s in st.steps if s.status == "running")
    overlay = f"{int(val * 100)}%  ·  {done} ok / {failed} fail / {running} run"
    dpg.set_value(f"{tag}::progress", val)
    dpg.configure_item(f"{tag}::progress", overlay=overlay)
    dpg.set_value(
        f"{tag}::counts",
        f"  {len(_visible_steps(tag))}/{len(st.steps)} shown",
    )


def _visible_steps(tag: str) -> list[Step]:
    st = _state(tag)
    if st.filter_status == "all":
        return list(st.steps)
    return [s for s in st.steps if s.status == st.filter_status]


def _rebuild_table(tag: str) -> None:
    table = f"{tag}::table"
    children = dpg.get_item_children(table, slot=1) or []
    for child in children:
        dpg.delete_item(child)

    for step in _visible_steps(tag):
        with dpg.table_row(parent=table):
            dpg.add_selectable(
                label=step.id,
                span_columns=True,
                callback=lambda s, a, u, t=tag: _on_select_step(t, u),
                user_data=step.id,
            )
            dpg.add_text(step.name)
            dpg.add_text(step.status)
            dpg.add_text(str(step.duration_ms) if step.duration_ms else "—")

    _refresh_progress(tag)


def _on_filter(tag: str, app_data) -> None:
    _state(tag).filter_status = str(app_data or "all")
    _rebuild_table(tag)


def _on_select_step(tag: str, step_id) -> None:
    sid = str(step_id)
    st = _state(tag)
    st.selected_step = sid
    step = next((s for s in st.steps if s.id == sid), None)
    if step is None:
        return
    dpg.set_value(f"{tag}::detail_title", f"{step.id} — {step.name}")
    dpg.set_value(
        f"{tag}::detail_body",
        f"status: {step.status}\n"
        f"duration_ms: {step.duration_ms or 'n/a'}\n"
        f"detail: {step.detail or '—'}\n"
        f"agent: {dpg.get_value(f'{tag}::agent')}",
    )


def _advance_step(tag: str) -> None:
    st = _state(tag)
    running = next((s for s in st.steps if s.status == "running"), None)
    if running is not None:
        running.status = "done"
        running.duration_ms = max(running.duration_ms, 200 + 40 * st.tick)
        running.detail = running.detail or "completed"
        st.log_lines.append(f"[tick {st.tick:02d}] completed {running.id}")
    nxt = next((s for s in st.steps if s.status == "queued"), None)
    if nxt is not None:
        nxt.status = "running"
        nxt.detail = "streaming…"
        st.log_lines.append(f"[tick {st.tick:02d}] started {nxt.id}")
    st.tick += 1
    _rebuild_table(tag)
    _refresh_log(tag)
    if st.selected_step:
        _on_select_step(tag, st.selected_step)


def _fail_selected(tag: str) -> None:
    st = _state(tag)
    sid = st.selected_step
    if not sid:
        st.log_lines.append(f"[tick {st.tick:02d}] fail ignored (no selection)")
        _refresh_log(tag)
        return
    step = next((s for s in st.steps if s.id == sid), None)
    if step is None:
        return
    step.status = "failed"
    step.detail = "operator marked failed"
    st.log_lines.append(f"[tick {st.tick:02d}] FAILED {step.id}")
    st.tick += 1
    _rebuild_table(tag)
    _refresh_log(tag)
    _on_select_step(tag, sid)


def _append_log_tick(tag: str) -> None:
    st = _state(tag)
    st.tick += 1
    st.log_lines.append(f"[tick {st.tick:02d}] heartbeat ok")
    _refresh_log(tag)


def _clear_log(tag: str) -> None:
    _state(tag).log_lines.clear()
    _refresh_log(tag)


def _refresh_log(tag: str) -> None:
    dpg.set_value(f"{tag}::log", "\n".join(_state(tag).log_lines))


def _reset_demo(tag: str) -> None:
    _STATES[tag] = _demo_state()
    st = _STATES[tag]
    dpg.set_value(f"{tag}::run_id", st.run_id)
    dpg.set_value(f"{tag}::agent", st.agent)
    dpg.set_value(f"{tag}::filter", "all")
    dpg.set_value(f"{tag}::notes", "")
    dpg.set_value(f"{tag}::detail_title", "(none)")
    dpg.set_value(f"{tag}::detail_body", "Select a row to inspect.")
    _rebuild_table(tag)
    _refresh_log(tag)


def main() -> None:
    dpg.create_context()
    build_ui()
    dpg.create_viewport(
        title=LABEL,
        width=WIN_W + 16,
        height=WIN_H + 40,
        resizable=False,
    )
    dpg.setup_dearpygui()
    dpg.set_primary_window(TAG, True)
    dpg.show_viewport()
    dpg.start_dearpygui()
    dpg.destroy_context()


if __name__ == "__main__":
    main()
