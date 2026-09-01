"""Regression tests for the shared frame pump.

Every FE in the workflow updates through a background thread → queue → per-frame
drain, and that drain is one module-global pump shared by every node on the
board. If it stops ticking, nothing on the canvas updates and no GUI test can
pass, so these run before anything boots the full canvas.

Both failures were measured, not hypothesized:

* arming at the literal frame ``1`` gives 30 ticks when registration happens
  before the first frame and **0 ticks, forever**, when it happens at frame 30;
* without a reset, module state outlives ``destroy_context()``, so a second
  context in the same process gets 0 ticks while callbacks from the destroyed
  first context stay registered.
"""

from __future__ import annotations

import contextlib
from typing import Iterator

from megadesk_contracts import host as dpg
import pytest
from megadesk_contracts import frame_pump

FRAMES = 30


@contextlib.contextmanager
def dpg_session(*, width: int = 320, height: int = 240) -> Iterator[None]:
    """A minimal shown-but-off-screen DPG context.

    Off-screen rather than minimized: a minimized viewport renders no frames at
    all, which would make every measurement here read as zero.
    """
    dpg.create_context()
    dpg.create_viewport(
        title="frame pump probe", width=width, height=height, x_pos=-2400, y_pos=0
    )
    dpg.setup_dearpygui()
    dpg.show_viewport()
    try:
        yield
    finally:
        frame_pump.reset()
        dpg.destroy_context()


def render(frames: int = FRAMES) -> None:
    for _ in range(frames):
        dpg.render_dearpygui_frame()


class Counter:
    def __init__(self) -> None:
        self.ticks = 0

    def __call__(self) -> None:
        self.ticks += 1


@pytest.fixture(autouse=True)
def clean_pump() -> Iterator[None]:
    frame_pump.reset()
    yield
    frame_pump.reset()


@pytest.mark.canvas
def test_registration_before_the_first_frame_ticks_every_frame() -> None:
    counter = Counter()
    with dpg_session():
        frame_pump.register(counter)
        render()
    assert counter.ticks >= FRAMES - 2, (
        f"expected about {FRAMES} ticks over {FRAMES} frames, got {counter.ticks}"
    )


@pytest.mark.canvas
def test_registration_after_the_first_frame_still_ticks() -> None:
    """The empty-board case: the first node is dropped long after frame 1.

    An absolute ``set_frame_callback(1, ...)`` schedules the pump for a frame
    that has already rendered, so it never fires — while the armed flag still
    flips, killing the pump for every node for the rest of the session.
    """
    counter = Counter()
    with dpg_session():
        render()
        assert dpg.get_frame_count() >= FRAMES
        frame_pump.register(counter)
        render()
    assert counter.ticks >= FRAMES - 2, (
        f"pump registered at frame {FRAMES} produced {counter.ticks} ticks; "
        "the pump must arm relative to the current frame, not at frame 1"
    )


@pytest.mark.canvas
def test_late_registration_reaches_callbacks_registered_earlier() -> None:
    """A node dropped later must not stop the drain of nodes already on the board."""
    first = Counter()
    second = Counter()
    with dpg_session():
        frame_pump.register(first)
        render(10)
        frame_pump.register(second)
        render(10)
    assert first.ticks >= 18
    assert second.ticks >= 8


@pytest.mark.canvas
def test_unregister_stops_only_that_callback() -> None:
    staying = Counter()
    leaving = Counter()
    with dpg_session():
        frame_pump.register(staying)
        frame_pump.register(leaving)
        render(10)
        at_removal = leaving.ticks
        frame_pump.unregister(leaving)
        render(10)
    assert leaving.ticks == at_removal
    assert staying.ticks >= 18


@pytest.mark.canvas
def test_each_context_cycle_gets_its_own_live_pump() -> None:
    """Three create/destroy cycles in one process, as a test session does.

    Without ``frame_pump.reset()`` the armed flag survives teardown, so cycles
    two and three tick zero times and stale callbacks from destroyed contexts
    accumulate — silently swallowed by the pump's bare ``except``.
    """
    counters: list[Counter] = []
    for cycle in range(3):
        counter = Counter()
        with dpg_session():
            frame_pump.register(counter)
            render(10)
        counters.append(counter)
        assert counter.ticks >= 8, (
            f"context cycle {cycle + 1} got {counter.ticks} pump ticks"
        )

    # Earlier cycles' callbacks must be gone, not merely idle.
    frozen = [c.ticks for c in counters]
    with dpg_session():
        frame_pump.register(Counter())
        render(10)
    assert [c.ticks for c in counters] == frozen, (
        "a callback from a destroyed context is still being pumped"
    )


@pytest.mark.canvas
def test_reset_clears_callbacks_and_rearms() -> None:
    dropped = Counter()
    with dpg_session():
        frame_pump.register(dropped)
        render(5)
        before = dropped.ticks
        frame_pump.reset()

        fresh = Counter()
        frame_pump.register(fresh)
        render(10)

    assert dropped.ticks == before, "reset must drop previously registered callbacks"
    assert fresh.ticks >= 8, "reset must leave the pump able to arm again"
