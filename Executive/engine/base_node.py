"""Base contract for all canvas GUI nodes (Dear PyGui)."""

from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

import dearpygui.dearpygui as dpg

MIN_SCALE = 0.15
HANDLE_HALF = 6.0  # screen-space half-size; converted via zoom when hit-testing


def _parse_scale(raw: Any) -> tuple[float, float]:
    """Accept float, [sx, sy], or {x,y} / {scale_x,scale_y}."""
    if isinstance(raw, (list, tuple)) and len(raw) >= 2:
        return float(raw[0]), float(raw[1])
    if isinstance(raw, dict):
        sx = raw.get("x", raw.get("scale_x", 1.0))
        sy = raw.get("y", raw.get("scale_y", 1.0))
        return float(sx), float(sy)
    try:
        s = float(raw)
        return s, s
    except (TypeError, ValueError):
        return 1.0, 1.0


class BaseNode(ABC):
    """Parent class every deployable node must inherit.

    Static fields describe the node type (sidebar entry).
    Instance fields describe a placed object on the canvas.
    """

    # --- static (type-level) ---
    nickname: str = "Node"
    global_guid: str = ""
    icon: str = ""
    description: str = ""

    # Spatial frame: children move with this node and hit-test ranks below contents.
    is_container: bool = False

    has_parent_limit: bool = False
    parent_limit: int = 0
    has_child_limit: bool = False
    child_limit: int = 0

    # Default local size in world units (overridable per type)
    default_width: float = 160.0
    default_height: float = 160.0

    @classmethod
    def resolve_icon_path(cls) -> Optional[str]:
        """Return an existing icon file path, or None to use the host default.

        ``icon`` may be absolute, CWD-relative, or relative to the module that
        defines the node class. Empty / missing / unloadable paths fall back.
        """
        raw = (cls.icon or "").strip()
        if not raw:
            return None

        path = Path(raw)
        if path.is_file():
            return str(path.resolve())

        try:
            module_dir = Path(inspect.getfile(cls)).resolve().parent
        except (TypeError, OSError):
            return None

        candidate = (module_dir / raw).resolve()
        if candidate.is_file():
            return str(candidate)
        return None

    def __init__(
        self,
        canvas_id: Optional[str] = None,
        position: Optional[tuple[float, float]] = None,
        scale: Any = 1.0,
        scale_x: Optional[float] = None,
        scale_y: Optional[float] = None,
        parents: Optional[list[str]] = None,
        children: Optional[list[str]] = None,
        data: Optional[dict[str, Any]] = None,
    ) -> None:
        self.canvas_id: str = canvas_id or str(uuid4())
        self.position: list[float] = list(position or (0.0, 0.0))
        sx, sy = _parse_scale(scale)
        self.scale_x: float = float(scale_x) if scale_x is not None else sx
        self.scale_y: float = float(scale_y) if scale_y is not None else sy
        self.parents: list[str] = list(parents or [])
        self.children: list[str] = list(children or [])
        self.data: dict[str, Any] = dict(data or {})
        self._selected: bool = False
        self.width: float = float(self.data.get("width", self.default_width))
        self.height: float = float(self.data.get("height", self.default_height))

    @property
    def scale(self) -> float:
        """Uniform-scale fallback (average of axes) for older node code."""
        return (self.scale_x + self.scale_y) * 0.5

    @scale.setter
    def scale(self, value: float) -> None:
        self.scale_x = float(value)
        self.scale_y = float(value)

    # --- serialization ---

    def to_member_dict(self) -> dict[str, Any]:
        self.data["width"] = self.width
        self.data["height"] = self.height
        return {
            "canvas_id": self.canvas_id,
            "type": self.global_guid,
            "nickname": self.nickname,
            "position": [float(self.position[0]), float(self.position[1])],
            "scale": [float(self.scale_x), float(self.scale_y)],
            "parents": list(self.parents),
            "children": list(self.children),
            "data": dict(self.data),
        }

    @classmethod
    def from_member_dict(cls, member: dict[str, Any]) -> "BaseNode":
        return cls(
            canvas_id=member.get("canvas_id"),
            position=tuple(member.get("position", (0.0, 0.0))),
            scale=member.get("scale", 1.0),
            parents=member.get("parents"),
            children=member.get("children"),
            data=member.get("data"),
        )

    # --- geometry helpers ---

    def bounds(self) -> tuple[float, float, float, float]:
        """Return world-space AABB as (x, y, w, h) of top-left origin."""
        w = self.width * self.scale_x
        h = self.height * self.scale_y
        return self.position[0], self.position[1], w, h

    def center(self) -> tuple[float, float]:
        x, y, w, h = self.bounds()
        return x + w * 0.5, y + h * 0.5

    def contains_point(self, x: float, y: float) -> bool:
        bx, by, bw, bh = self.bounds()
        return bx <= x <= bx + bw and by <= y <= by + bh

    def move_by(self, dx: float, dy: float) -> None:
        self.position[0] += dx
        self.position[1] += dy

    def handle_centers(self) -> dict[str, tuple[float, float]]:
        """World-space centers of the eight perimeter resize handles."""
        x, y, w, h = self.bounds()
        return {
            "nw": (x, y),
            "n": (x + w * 0.5, y),
            "ne": (x + w, y),
            "e": (x + w, y + h * 0.5),
            "se": (x + w, y + h),
            "s": (x + w * 0.5, y + h),
            "sw": (x, y + h),
            "w": (x, y + h * 0.5),
        }

    def hit_resize_handle(
        self, world_x: float, world_y: float, zoom: float = 1.0
    ) -> Optional[str]:
        """Return handle id under the point, or None."""
        half = HANDLE_HALF / max(zoom, 1e-6)
        for hid, (hx, hy) in self.handle_centers().items():
            if abs(world_x - hx) <= half and abs(world_y - hy) <= half:
                return hid
        return None

    def resize_to_point(self, handle: str, world_x: float, world_y: float) -> None:
        """Resize by dragging a perimeter handle; updates scale_x/scale_y and position."""
        x, y, w, h = self.bounds()
        right = x + w
        bottom = y + h
        min_w = self.width * MIN_SCALE
        min_h = self.height * MIN_SCALE

        new_left, new_right = x, right
        new_top, new_bottom = y, bottom

        if handle in ("nw", "sw", "w"):
            new_left = min(world_x, right - min_w)
        if handle in ("ne", "se", "e"):
            new_right = max(world_x, x + min_w)
        if handle in ("nw", "ne", "n"):
            new_top = min(world_y, bottom - min_h)
        if handle in ("sw", "se", "s"):
            new_bottom = max(world_y, y + min_h)

        new_w = max(min_w, new_right - new_left)
        new_h = max(min_h, new_bottom - new_top)

        self.position[0] = new_left
        self.position[1] = new_top
        self.scale_x = new_w / max(self.width, 1e-6)
        self.scale_y = new_h / max(self.height, 1e-6)

    def draw_resize_handles(self, drawlist: str | int, world_to_screen) -> None:
        """Draw selection chrome + eight perimeter resize boxes."""
        x, y, w, h = self.bounds()
        pmin = world_to_screen(x, y)
        pmax = world_to_screen(x + w, y + h)
        dpg.draw_rectangle(
            (pmin[0] - 3, pmin[1] - 3),
            (pmax[0] + 3, pmax[1] + 3),
            color=(80, 160, 255, 220),
            fill=(0, 0, 0, 0),
            thickness=2,
            parent=drawlist,
        )
        half = HANDLE_HALF
        for hx, hy in self.handle_centers().values():
            sx, sy = world_to_screen(hx, hy)
            dpg.draw_rectangle(
                (sx - half, sy - half),
                (sx + half, sy + half),
                color=(40, 90, 180, 255),
                fill=(240, 248, 255, 255),
                thickness=1.5,
                parent=drawlist,
            )

    # --- drawing ---

    @abstractmethod
    def draw(
        self,
        drawlist: str | int,
        world_to_screen,
        selected: bool = False,
    ) -> None:
        """Draw this node into the canvas drawlist using world_to_screen(x,y)->(sx,sy)."""

    # --- interface hooks ---

    def on_select(self) -> None:
        self._selected = True

    def on_deselect(self) -> None:
        self._selected = False

    def on_start_drag(self) -> None:
        pass

    def on_drag(self, dx: float, dy: float) -> None:
        self.move_by(dx, dy)

    def on_end_drag(self) -> None:
        pass

    def on_start_resize(self, handle: str) -> None:
        pass

    def on_resize(self, handle: str, world_x: float, world_y: float) -> None:
        self.resize_to_point(handle, world_x, world_y)

    def on_end_resize(self) -> None:
        pass

    def on_create(self) -> None:
        pass

    def on_destroy(self) -> None:
        pass

    def on_object_enter(self, other_id: str) -> None:
        if other_id not in self.children:
            self.children.append(other_id)

    def on_object_exit(self, other_id: str) -> None:
        if other_id in self.children:
            self.children.remove(other_id)

    def on_double_click(self) -> None:
        """Optional override for in-place editing."""
        pass
