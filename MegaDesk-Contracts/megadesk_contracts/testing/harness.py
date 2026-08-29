"""Boot the real MegaDesk canvas under test control.

The canvas and every FE live in one Python process — FEs are ``dpg.node`` items
inside the canvas ``node_editor``, not separate windows — so a test drives the
same code a click would: it addresses widgets by tag, invokes their real bound
callbacks, and advances frames itself instead of handing control to
``while dpg.is_dearpygui_running()``.

The viewport is positioned off-screen rather than minimized: a minimized
viewport renders nothing and ``output_frame_buffer`` writes an empty PNG. That
means these tests need a desktop session; they are not headless.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import dearpygui.dearpygui as dpg

from megadesk_contracts import frame_pump
from megadesk_contracts.testing.driver import NodeDriver

# Far enough left to be off any real monitor, but still a shown viewport.
OFFSCREEN_VIEWPORT_POS = (-2400, 0)
DEFAULT_TIMEOUT_SEC = 10.0


class HarnessTimeout(AssertionError):
    """A ``wait_until`` predicate never became true."""


class PumpProbe:
    """Counts shared frame-pump ticks.

    Registered like any FE's per-frame drain, so it goes dead exactly when the
    real FEs' drains do.
    """

    def __init__(self) -> None:
        self.ticks = 0
        frame_pump.register(self._tick)

    def _tick(self) -> None:
        self.ticks += 1

    def remove(self) -> None:
        frame_pump.unregister(self._tick)


def _canvas_api() -> tuple[Any, Any, Any, Any, Any]:
    """Import the canvas construction path, or explain why it is not importable."""
    try:
        from engine.display_engine import NODE_EDITOR
        from engine.graph_model import GraphModel
        from engine.megadesk_registry import (
            discover_megadesk_frontends,
            palette_key,
        )
        from main import build_canvas
    except ImportError as exc:  # pragma: no cover - environment problem
        raise RuntimeError(
            "MegaDesk-Canvas is not importable. Put the MegaDesk-Canvas directory "
            "on sys.path (it holds engine/ and main.py) before booting "
            f"CanvasHarness. Original error: {exc}"
        ) from exc
    return GraphModel, NODE_EDITOR, discover_megadesk_frontends, palette_key, build_canvas


class CanvasHarness:
    """A booted canvas with deliberate time control.

    Typical use::

        with CanvasHarness(graph_path=tmp_path / "graph.json") as harness:
            d = harness.drop("work_dispatcher")
            d.type_into("git_url", "https://github.com/acme/widgets")
            harness.wait_until(lambda: d.get("status_text") != "Idle")
    """

    def __init__(
        self,
        *,
        graph_path: Path,
        width: int = 1280,
        height: int = 800,
        viewport_pos: Optional[tuple[int, int]] = OFFSCREEN_VIEWPORT_POS,
        supervisor_panel: bool = False,
        graph_bar: bool = True,
        artifacts_dir: Optional[Path] = None,
        warmup_frames: int = 5,
    ) -> None:
        self.graph_path = Path(graph_path)
        self.width = width
        self.height = height
        self.viewport_pos = viewport_pos
        self.supervisor_panel = supervisor_panel
        self.graph_bar = graph_bar
        self.artifacts_dir = Path(artifacts_dir) if artifacts_dir else None
        self.warmup_frames = warmup_frames

        self.model: Any = None
        self.engine: Any = None
        self.frames = 0
        self._booted = False
        self._shot_seq = 0
        self._drivers: dict[str, NodeDriver] = {}

        (
            self._GraphModel,
            self._node_editor,
            self._discover,
            self._palette_key,
            self._build_canvas,
        ) = _canvas_api()

    # --- lifecycle ---

    def boot(self) -> "CanvasHarness":
        if self._booted:
            return self
        self._discover()
        self.model = self._GraphModel(self.graph_path)
        self.model.load()
        self.engine = self._build_canvas(
            self.model,
            width=self.width,
            height=self.height,
            viewport_pos=self.viewport_pos,
            supervisor_panel=self.supervisor_panel,
            graph_bar=self.graph_bar,
        )
        self._booted = True
        self._sync_drivers()
        self.pump(self.warmup_frames)
        return self

    def shutdown(self) -> None:
        """Tear the board and the DPG context down, leaving no module state behind.

        Members are deleted first so each FE's cleanup callable runs and its
        polling thread stops; ``frame_pump.reset()`` then clears the shared pump,
        which outlives ``destroy_context()``.
        """
        if not self._booted:
            return
        try:
            self.clear_board()
        finally:
            self._drivers.clear()
            frame_pump.reset()
            try:
                dpg.destroy_context()
            except Exception:
                pass
            self._booted = False
            self.model = None
            self.engine = None

    def __enter__(self) -> "CanvasHarness":
        return self.boot()

    def __exit__(self, *_exc: object) -> None:
        self.shutdown()

    # --- time ---

    def pump(self, n: int = 1) -> int:
        """Render ``n`` frames, running the engine sync the real loop runs."""
        if not self._booted:
            raise RuntimeError("CanvasHarness.boot() must run before pumping frames")
        rendered = 0
        for _ in range(max(0, int(n))):
            if not dpg.is_dearpygui_running():
                break
            self.engine.sync_members()
            dpg.render_dearpygui_frame()
            self.frames += 1
            rendered += 1
        return rendered

    def wait_until(
        self,
        predicate: Callable[[], bool],
        *,
        timeout: float = DEFAULT_TIMEOUT_SEC,
        message: str = "",
        screenshot: bool = True,
    ) -> None:
        """Pump frames until ``predicate`` holds; raise ``HarnessTimeout`` if it never does.

        Every FE in this workflow updates through a background thread into a
        queue drained by the shared frame pump, so a fixed frame count is a race
        that can read as "no bug" when the pump is in fact dead.
        """
        deadline = time.monotonic() + float(timeout)
        while True:
            if predicate():
                return
            if time.monotonic() >= deadline:
                detail = message or getattr(predicate, "__doc__", "") or "predicate"
                artifact = ""
                if screenshot:
                    try:
                        artifact = f" (screenshot: {self.screenshot('timeout')})"
                    except Exception as exc:  # pragma: no cover - artifact best effort
                        artifact = f" (screenshot failed: {exc})"
                raise HarnessTimeout(
                    f"Timed out after {timeout:.1f}s waiting for {detail}{artifact}"
                )
            self.pump(1)

    def wait_for_value(
        self,
        driver: NodeDriver,
        suffix: str,
        expected: Any,
        *,
        timeout: float = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self.wait_until(
            lambda: driver.exists(suffix) and driver.get(suffix) == expected,
            timeout=timeout,
            message=f"{driver.node_name} {suffix!r} to become {expected!r}",
        )

    def wait_for_widget(
        self,
        driver: NodeDriver,
        suffix: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self.wait_until(
            lambda: driver.exists(suffix),
            timeout=timeout,
            message=f"{driver.node_name} widget {suffix!r} to appear",
        )

    # --- board ---

    def drop(
        self,
        node_name: str,
        *,
        position: Any = "auto",
        settle_frames: int = 3,
    ) -> NodeDriver:
        """Drop a Catalog node onto the board through the engine's real drop path.

        ``position`` defaults to a deterministic grid slot so nodes do not stack
        on top of each other in screenshots; pass ``None`` to keep whatever the
        drop computed from the mouse.
        """
        before = set(self.model.members)
        self.engine.on_graph_drop(
            self._node_editor, self._palette_key(node_name), None
        )
        created = set(self.model.members) - before
        if not created:
            raise AssertionError(
                f"Dropping {node_name!r} added no member to the board. "
                "Is the node installed and discoverable via MegaDesk.nodes?"
            )
        member_id = created.pop()

        if position == "auto":
            position = self._next_position(len(before))
        if position is not None:
            self._place(member_id, position)

        self._sync_drivers()
        self.pump(settle_frames)
        return self._drivers[member_id]

    def load_graph(self, path: Path) -> None:
        """Open another graph through the engine's real load path."""
        self.graph_path = Path(path)
        self.engine.load_graph(self.graph_path)
        self._sync_drivers()
        self.pump(3)

    def _sync_drivers(self) -> None:
        members = getattr(self.model, "members", None) or {}
        self._drivers = {
            member_id: NodeDriver(self, member_id, member.name)
            for member_id, member in members.items()
        }

    def _next_position(self, index: int) -> tuple[float, float]:
        column, row = divmod(index, 3)
        return (40.0 + column * 520.0, 40.0 + row * 250.0)

    def _place(self, member_id: str, position: Iterable[float]) -> None:
        x, y = (float(v) for v in position)
        member = self.model.members.get(member_id)
        if member is None:
            return
        member.position = [x, y]
        tag = member.hosted_tag()
        if dpg.does_item_exist(tag):
            dpg.set_item_pos(tag, [x, y])

    def drivers(self) -> list[NodeDriver]:
        return [
            d
            for member_id, d in self._drivers.items()
            if member_id in (self.model.members or {})
        ]

    def driver_for(self, node_name: str) -> NodeDriver:
        for driver in self.drivers():
            if driver.node_name == node_name:
                return driver
        raise AssertionError(f"No {node_name!r} node on the board")

    def clear_board(self) -> None:
        """Remove every member, running each FE's cleanup."""
        if self.model is None:
            return
        for member_id in list(self.model.members):
            try:
                self.model.delete_node(member_id)
            except Exception:
                pass
        self._drivers.clear()
        if self._booted:
            self.pump(2)

    # --- observation ---

    def install_pump_probe(self) -> PumpProbe:
        """Register a counter on the shared frame pump."""
        return PumpProbe()

    def screenshot(self, name: str = "canvas") -> Path:
        """Write the current frame buffer to a PNG an agent can read back."""
        directory = self.artifacts_dir or Path.cwd()
        directory.mkdir(parents=True, exist_ok=True)
        self._shot_seq += 1
        path = directory / f"{name}-{self._shot_seq:02d}.png"
        self.pump(1)
        dpg.output_frame_buffer(str(path))
        self.pump(2)
        return path
