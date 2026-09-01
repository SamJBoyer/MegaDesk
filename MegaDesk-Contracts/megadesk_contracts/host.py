"""Toolkit-agnostic widget host for MegaDesk front-ends.

FEs and canvas chrome address widgets by string tag. Integration tests and
``NodeDriver`` read, write, and click through this registry. When the canvas
runs visually it also binds NiceGUI elements onto the same widgets.
"""

from __future__ import annotations

import base64
import inspect
import logging
import mimetypes
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

log = logging.getLogger("megadesk.host")

# Dear PyGui leftovers kept so existing callback / theme constants still import.
mvAll = 0
mvButton = 1
mvChildWindow = 2
mvNode = 3
mvComboHeight_Small = 0
mvMouseButton_Left = 0
mvMouseButton_Right = 1
mvNode_Attr_Static = 0
mvThemeCat_Nodes = 0
mvKey_Delete = "Delete"
mvThemeCol_WindowBg = 0
mvThemeCol_ChildBg = 1
mvThemeCol_PopupBg = 2
mvThemeCol_Border = 3
mvThemeCol_Text = 4
mvThemeCol_TextDisabled = 5
mvThemeCol_FrameBg = 6
mvThemeCol_FrameBgHovered = 7
mvThemeCol_FrameBgActive = 8
mvThemeCol_TitleBg = 9
mvThemeCol_TitleBgActive = 10
mvThemeCol_Button = 11
mvThemeCol_ButtonHovered = 12
mvThemeCol_ButtonActive = 13
mvThemeCol_Header = 14
mvThemeCol_HeaderHovered = 15
mvThemeCol_HeaderActive = 16
mvThemeCol_CheckMark = 17
mvThemeCol_ScrollbarBg = 18
mvThemeCol_ScrollbarGrab = 19
mvThemeCol_Separator = 20
mvStyleVar_FrameRounding = 0
mvStyleVar_WindowRounding = 1
mvStyleVar_FramePadding = 2
mvStyleVar_WindowPadding = 3
mvStyleVar_ItemSpacing = 4
mvStyleVar_ChildBorderSize = 5
mvNodeStyleVar_NodePadding = 0
mvNodeMiniMap_Location_BottomRight = 3

_WIDGETS: dict[str, "Widget"] = {}
_STACK: list[str] = []
_ANON = 0
_VISUAL = False
_CLIPBOARD = ""
_SELECTED: list[str] = []
_HOVERED: set[str] = set()
_VIEWPORT = [1280, 800]
_RUNNING = False
_FRAME = 0
_FRAME_CALLBACKS: dict[int, list[Callable[..., Any]]] = {}
_RESIZE_CALLBACK: Callable[..., Any] | None = None
_FOCUSED: Optional[str] = None
_LAST_IMAGE_PATH: Any = None


@dataclass
class Widget:
    tag: str
    kind: str
    parent: Optional[str] = None
    value: Any = None
    label: str = ""
    items: list[Any] = field(default_factory=list)
    callback: Callable[..., Any] | None = None
    drop_callback: Callable[..., Any] | None = None
    payload_type: str = ""
    user_data: Any = None
    show: bool = True
    enabled: bool = True
    color: Any = None
    children: list[str] = field(default_factory=list)
    pos: list[float] = field(default_factory=lambda: [0.0, 0.0])
    width: Any = None
    height: Any = None
    extra: dict[str, Any] = field(default_factory=dict)
    element: Any = None


def set_visual(on: bool) -> None:
    """Enable NiceGUI element creation. Call from inside a ``@ui.page``."""
    global _VISUAL
    _VISUAL = bool(on)


def is_visual() -> bool:
    return _VISUAL


def reset() -> None:
    """Drop every widget. Call on canvas teardown (and between harness boots)."""
    global _ANON, _CLIPBOARD, _FOCUSED
    for widget in list(_WIDGETS.values()):
        widget.element = None
    _WIDGETS.clear()
    _STACK.clear()
    _SELECTED.clear()
    _HOVERED.clear()
    _ANON = 0
    _CLIPBOARD = ""
    _FOCUSED = None


def _ui() -> Any:
    if not _VISUAL:
        return None
    from nicegui import ui

    return ui


def _anon(prefix: str = "anon") -> str:
    global _ANON
    _ANON += 1
    return f"__md_{prefix}_{_ANON}"


def _parent_of(explicit: Any = None) -> Optional[str]:
    if explicit not in (None, 0, ""):
        return str(explicit)
    return _STACK[-1] if _STACK else None


def _link(widget: Widget) -> Widget:
    _WIDGETS[widget.tag] = widget
    if widget.parent and widget.parent in _WIDGETS:
        parent = _WIDGETS[widget.parent]
        if widget.tag not in parent.children:
            parent.children.append(widget.tag)
    _attach_visual(widget)
    return widget


def _create(
    kind: str,
    *,
    tag: str | None = None,
    parent: Any = None,
    **kwargs: Any,
) -> Widget:
    ident = str(tag) if tag else _anon(kind)
    widget = Widget(tag=ident, kind=kind, parent=_parent_of(parent), **kwargs)
    return _link(widget)


def invoke(callback: Callable[..., Any] | None, sender: Any, app_data: Any, user_data: Any) -> Any:
    """Call a widget callback with as many of (sender, app_data, user_data) as it takes."""
    if callback is None:
        return None
    args = (sender, app_data, user_data)
    try:
        signature = inspect.signature(callback)
    except (TypeError, ValueError):
        return callback(*args)
    accepted = 0
    for param in signature.parameters.values():
        if param.kind is inspect.Parameter.VAR_POSITIONAL:
            return callback(*args)
        if param.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            accepted += 1
    return callback(*args[:accepted])


def fire(tag: str, app_data: Any = None) -> Any:
    widget = _require(tag)
    return invoke(widget.callback, widget.tag, app_data, widget.user_data)


def fire_drop(tag: str, app_data: Any = None) -> Any:
    widget = _require(tag)
    return invoke(widget.drop_callback, widget.tag, app_data, widget.user_data)


def _require(tag: Any) -> Widget:
    ident = str(tag)
    widget = _WIDGETS.get(ident)
    if widget is None:
        raise KeyError(ident)
    return widget


def does_item_exist(tag: Any) -> bool:
    return str(tag) in _WIDGETS


def get_aliases() -> list[str]:
    return list(_WIDGETS)


def get_item_alias(tag: Any) -> str:
    ident = str(tag)
    return ident if ident in _WIDGETS else ident


def get_item_parent(tag: Any) -> Optional[str]:
    widget = _WIDGETS.get(str(tag))
    return widget.parent if widget is not None else None


def get_item_children(tag: Any, slot: int = 1) -> list[str]:
    _ = slot
    widget = _WIDGETS.get(str(tag))
    return list(widget.children) if widget is not None else []


def get_item_callback(tag: Any) -> Callable[..., Any] | None:
    widget = _WIDGETS.get(str(tag))
    return widget.callback if widget is not None else None


def get_item_drop_callback(tag: Any) -> Callable[..., Any] | None:
    widget = _WIDGETS.get(str(tag))
    return widget.drop_callback if widget is not None else None


def get_item_user_data(tag: Any) -> Any:
    widget = _WIDGETS.get(str(tag))
    return widget.user_data if widget is not None else None


def set_item_user_data(tag: Any, data: Any) -> None:
    widget = _WIDGETS.get(str(tag))
    if widget is not None:
        widget.user_data = data


def get_item_configuration(tag: Any) -> dict[str, Any]:
    widget = _require(tag)
    return {
        "label": widget.label,
        "items": list(widget.items),
        "show": widget.show,
        "enabled": widget.enabled,
        "height": widget.height,
        "width": widget.width,
        "pos": list(widget.pos),
    }


def get_value(tag: Any) -> Any:
    widget = _WIDGETS.get(str(tag))
    return None if widget is None else widget.value


def set_value(tag: Any, value: Any) -> None:
    widget = _WIDGETS.get(str(tag))
    if widget is None:
        return
    widget.value = value
    _sync_visual(widget)


def configure_item(tag: Any, **kwargs: Any) -> None:
    widget = _WIDGETS.get(str(tag))
    if widget is None:
        return
    if "items" in kwargs and kwargs["items"] is not None:
        widget.items = list(kwargs["items"])
    if "label" in kwargs:
        widget.label = str(kwargs["label"] or "")
    if "show" in kwargs:
        widget.show = bool(kwargs["show"])
    if "enabled" in kwargs:
        widget.enabled = bool(kwargs["enabled"])
    if "width" in kwargs:
        widget.width = kwargs["width"]
    if "height" in kwargs:
        widget.height = kwargs["height"]
    if "color" in kwargs:
        widget.color = kwargs["color"]
    if "fill" in kwargs:
        widget.extra["fill"] = kwargs["fill"]
        widget.color = kwargs["fill"]
    if "pos" in kwargs and kwargs["pos"] is not None:
        pos = list(kwargs["pos"])
        widget.pos = [float(pos[0]), float(pos[1])]
    for key in ("hint", "readonly", "default_value"):
        if key in kwargs:
            widget.extra[key] = kwargs[key]
    _sync_visual(widget)


def set_item_pos(tag: Any, pos: list[float] | tuple[float, float]) -> None:
    configure_item(tag, pos=pos)


def get_item_pos(tag: Any) -> list[float]:
    widget = _WIDGETS.get(str(tag))
    return list(widget.pos) if widget is not None else [0.0, 0.0]


def set_item_width(tag: Any, width: int) -> None:
    configure_item(tag, width=width)


def set_item_height(tag: Any, height: int) -> None:
    configure_item(tag, height=height)


def delete_item(tag: Any, children_only: bool = False) -> None:
    ident = str(tag)
    widget = _WIDGETS.get(ident)
    if widget is None:
        return
    for child in list(widget.children):
        delete_item(child, children_only=False)
    widget.children.clear()
    if children_only:
        if widget.element is not None:
            try:
                widget.element.clear()
            except Exception:
                pass
        return
    if widget.parent and widget.parent in _WIDGETS:
        parent = _WIDGETS[widget.parent]
        if ident in parent.children:
            parent.children.remove(ident)
    if widget.element is not None:
        try:
            widget.element.delete()
        except Exception:
            pass
    _WIDGETS.pop(ident, None)


def move_item(tag: Any, parent: Any = None, before: Any = 0) -> None:
    ident = str(tag)
    widget = _WIDGETS.get(ident)
    if widget is None:
        return
    old = widget.parent
    if old and old in _WIDGETS and ident in _WIDGETS[old].children:
        _WIDGETS[old].children.remove(ident)
    new_parent = str(parent) if parent not in (None, 0, "") else None
    widget.parent = new_parent
    if new_parent and new_parent in _WIDGETS:
        siblings = _WIDGETS[new_parent].children
        if ident in siblings:
            siblings.remove(ident)
        index = len(siblings)
        if before not in (None, 0, ""):
            before_tag = str(before)
            if before_tag in siblings:
                index = siblings.index(before_tag)
        siblings.insert(index, ident)
    if widget.element is not None and new_parent and new_parent in _WIDGETS:
        target = _WIDGETS[new_parent].element
        if target is not None:
            try:
                widget.element.move(target)
            except Exception:
                pass


def get_clipboard_text() -> str:
    return _CLIPBOARD


def set_clipboard_text(text: str) -> None:
    global _CLIPBOARD
    _CLIPBOARD = str(text or "")
    ui = _ui()
    if ui is not None:
        try:
            ui.clipboard.write(_CLIPBOARD)
        except Exception:
            pass


def focus_item(tag: Any) -> None:
    global _FOCUSED
    ident = str(tag)
    _FOCUSED = ident
    widget = _WIDGETS.get(ident)
    if widget is not None and widget.element is not None:
        try:
            widget.element.run_method("focus")
        except Exception:
            pass


def is_item_hovered(tag: Any, **_kwargs: Any) -> bool:
    return str(tag) in _HOVERED


def set_hovered(tag: Any, hovered: bool = True) -> None:
    ident = str(tag)
    if hovered:
        _HOVERED.add(ident)
    else:
        _HOVERED.discard(ident)


def get_selected_nodes(_editor: Any = None) -> list[str]:
    return list(_SELECTED)


def clear_selected_nodes(_editor: Any = None) -> None:
    _SELECTED.clear()


def select_node(tag: str) -> None:
    ident = str(tag)
    if ident not in _SELECTED:
        _SELECTED[:] = [ident]
    widget = _WIDGETS.get(ident)
    if widget is not None:
        widget.extra["selected"] = True
        _sync_visual(widget)


def viewport_size() -> tuple[int, int]:
    return int(_VIEWPORT[0]), int(_VIEWPORT[1])


def set_viewport_size(width: int, height: int) -> None:
    _VIEWPORT[0] = int(width)
    _VIEWPORT[1] = int(height)


def get_viewport_client_width() -> int:
    return int(_VIEWPORT[0])


def get_viewport_client_height() -> int:
    return int(_VIEWPORT[1])


def get_frame_count() -> int:
    return int(_FRAME)


def get_drawing_mouse_pos() -> tuple[float, float]:
    return (0.0, 0.0)


def get_mouse_pos(local: bool = False) -> list[float]:
    _ = local
    return [0.0, 0.0]


def show_item(tag: Any) -> None:
    configure_item(tag, show=True)
    widget = _WIDGETS.get(str(tag))
    if widget is not None and widget.kind == "file_dialog" and widget.extra.get("on_show"):
        widget.extra["on_show"]()


def hide_item(tag: Any) -> None:
    configure_item(tag, show=False)


def split_frame() -> None:
    return


def bind_item_theme(_tag: Any, _theme: Any) -> None:
    return


def add_theme_color(*_args: Any, **_kwargs: Any) -> None:
    return


def add_theme_style(*_args: Any, **_kwargs: Any) -> None:
    return


def add_file_extension(*_args: Any, **_kwargs: Any) -> None:
    return


def mount(tag: str, element: Any, *, kind: str = "group", parent: Any = None, **kwargs: Any) -> Widget:
    """Register an already-created visual root (the NiceGUI page column)."""
    widget = Widget(
        tag=str(tag),
        kind=kind,
        parent=_parent_of(parent),
        element=element,
        **kwargs,
    )
    return _link(widget)


class _Context:
    def __init__(self, widget: Widget) -> None:
        self.widget = widget
        self.tag = widget.tag

    def __enter__(self) -> str:
        _STACK.append(self.widget.tag)
        return self.widget.tag

    def __exit__(self, *_exc: object) -> None:
        if _STACK and _STACK[-1] == self.widget.tag:
            _STACK.pop()


def group(
    parent: Any = None,
    horizontal: bool = False,
    tag: str | None = None,
    **kwargs: Any,
) -> _Context:
    widget = _create(
        "row" if horizontal else "column",
        tag=tag,
        parent=parent,
        extra={"horizontal": horizontal, **kwargs},
        payload_type=str(kwargs.get("payload_type") or ""),
        drop_callback=kwargs.get("drop_callback"),
        user_data=kwargs.get("user_data"),
        width=kwargs.get("width"),
        height=kwargs.get("height"),
    )
    return _Context(widget)


def child_window(
    parent: Any = None,
    tag: str | None = None,
    width: Any = None,
    height: Any = None,
    border: bool = True,
    payload_type: str = "",
    drop_callback: Callable[..., Any] | None = None,
    user_data: Any = None,
    **kwargs: Any,
) -> _Context:
    widget = _create(
        "child",
        tag=tag,
        parent=parent,
        width=width,
        height=height,
        payload_type=payload_type,
        drop_callback=drop_callback,
        user_data=user_data,
        extra={"border": border, **kwargs},
    )
    return _Context(widget)


def add_child_window(**kwargs: Any) -> str:
    ctx = child_window(**kwargs)
    return ctx.widget.tag


def tab_bar(parent: Any = None, tag: str | None = None, **kwargs: Any) -> _Context:
    widget = _create("tab_bar", tag=tag, parent=parent, extra=dict(kwargs))
    return _Context(widget)


def tab(label: str = "", tag: str | None = None, parent: Any = None, **kwargs: Any) -> _Context:
    bar = _parent_of(parent)
    widget = _create("tab", tag=tag, parent=bar, label=label, extra=dict(kwargs))
    if bar and bar in _WIDGETS:
        bar_w = _WIDGETS[bar]
        if bar_w.value in (None, ""):
            bar_w.value = widget.tag
    return _Context(widget)


def drawlist(
    width: int = 16,
    height: int = 16,
    tag: str | None = None,
    parent: Any = None,
    **kwargs: Any,
) -> _Context:
    widget = _create(
        "drawlist",
        tag=tag,
        parent=parent,
        width=width,
        height=height,
        extra=dict(kwargs),
    )
    return _Context(widget)


def add_drawlist(**kwargs: Any) -> str:
    ctx = drawlist(**kwargs)
    return ctx.widget.tag


def theme(tag: str | None = None, **_kwargs: Any) -> _Context:
    widget = _create("theme", tag=tag)
    return _Context(widget)


@contextmanager
def theme_component(*_args: Any, **_kwargs: Any) -> Iterator[None]:
    yield


def handler_registry(tag: str | None = None, **_kwargs: Any) -> _Context:
    widget = _create("handlers", tag=tag)
    return _Context(widget)


def add_handler_registry(tag: str | None = None, **kwargs: Any) -> str:
    return _create("handlers", tag=tag, extra=dict(kwargs)).tag


@contextmanager
def drag_payload(
    parent: Any = None,
    drag_data: Any = None,
    payload_type: str = "",
    tag: str | None = None,
    **_kwargs: Any,
) -> Iterator[str]:
    widget = _create(
        "payload",
        tag=tag,
        parent=parent,
        user_data=drag_data,
        payload_type=payload_type,
        show=False,
        extra={"drag_data": drag_data},
    )
    if parent and parent in _WIDGETS:
        _WIDGETS[str(parent)].payload_type = payload_type
        _WIDGETS[str(parent)].extra["drag_data"] = drag_data
    _STACK.append(widget.tag)
    try:
        yield widget.tag
    finally:
        if _STACK and _STACK[-1] == widget.tag:
            _STACK.pop()


@contextmanager
def tooltip(parent: Any = None, **_kwargs: Any) -> Iterator[None]:
    widget = _create("tooltip", parent=parent, show=False)
    _STACK.append(widget.tag)
    try:
        yield
    finally:
        if _STACK and _STACK[-1] == widget.tag:
            _STACK.pop()


def file_dialog(
    tag: str | None = None,
    show: bool = False,
    callback: Callable[..., Any] | None = None,
    **kwargs: Any,
) -> _Context:
    widget = _create(
        "file_dialog",
        tag=tag,
        show=show,
        callback=callback,
        extra=dict(kwargs),
    )
    return _Context(widget)


def node(
    label: str = "",
    pos: list[float] | None = None,
    tag: str | None = None,
    parent: Any = None,
    show: bool = True,
    **kwargs: Any,
) -> _Context:
    widget = _create(
        "node",
        tag=tag,
        parent=parent,
        label=label,
        pos=list(pos or [0.0, 0.0]),
        show=show,
        extra=dict(kwargs),
    )
    return _Context(widget)


def add_node(
    label: str = "",
    pos: list[float] | None = None,
    tag: str | None = None,
    parent: Any = None,
    show: bool = True,
    **kwargs: Any,
) -> str:
    return node(
        label=label, pos=pos, tag=tag, parent=parent, show=show, **kwargs
    ).widget.tag


@contextmanager
def node_attribute(*_args: Any, **_kwargs: Any) -> Iterator[None]:
    yield


def node_editor(tag: str | None = None, parent: Any = None, **kwargs: Any) -> _Context:
    widget = _create("editor", tag=tag, parent=parent, extra=dict(kwargs))
    return _Context(widget)


def window(
    label: str = "",
    tag: str | None = None,
    parent: Any = None,
    pos: Any = None,
    width: Any = None,
    height: Any = None,
    **kwargs: Any,
) -> _Context:
    position = [0.0, 0.0]
    if pos is not None:
        position = [float(pos[0]), float(pos[1])]
    widget = _create(
        "window",
        tag=tag,
        parent=parent,
        label=label,
        pos=position,
        width=width,
        height=height,
        extra=dict(kwargs),
    )
    return _Context(widget)


def add_input_text(
    tag: str | None = None,
    default_value: str = "",
    callback: Callable[..., Any] | None = None,
    on_enter: bool = False,
    multiline: bool = False,
    readonly: bool = False,
    width: Any = None,
    height: Any = None,
    hint: str = "",
    show: bool = True,
    parent: Any = None,
    **kwargs: Any,
) -> str:
    return _create(
        "textarea" if multiline else "input",
        tag=tag,
        parent=parent,
        value=default_value,
        callback=callback,
        width=width,
        height=height,
        show=show,
        extra={
            "on_enter": on_enter,
            "readonly": readonly,
            "hint": hint,
            "multiline": multiline,
            **kwargs,
        },
    ).tag


def add_button(
    label: str = "",
    tag: str | None = None,
    callback: Callable[..., Any] | None = None,
    user_data: Any = None,
    width: Any = None,
    height: Any = None,
    parent: Any = None,
    enabled: bool = True,
    small: bool = False,
    **kwargs: Any,
) -> str:
    return _create(
        "button",
        tag=tag,
        parent=parent,
        label=label,
        callback=callback,
        user_data=user_data,
        width=width,
        height=height,
        enabled=enabled,
        extra={"small": small, **kwargs},
    ).tag


def add_combo(
    items: list[Any] | None = None,
    default_value: Any = None,
    tag: str | None = None,
    callback: Callable[..., Any] | None = None,
    width: Any = None,
    parent: Any = None,
    **kwargs: Any,
) -> str:
    options = [str(item) for item in (items or [])]
    value = default_value if default_value is not None else (options[0] if options else "")
    return _create(
        "combo",
        tag=tag,
        parent=parent,
        items=options,
        value=value,
        callback=callback,
        width=width,
        extra=dict(kwargs),
    ).tag


def add_listbox(
    items: list[Any] | None = None,
    tag: str | None = None,
    callback: Callable[..., Any] | None = None,
    num_items: int = 2,
    width: Any = None,
    parent: Any = None,
    **kwargs: Any,
) -> str:
    options = [str(item) for item in (items or [])]
    return _create(
        "listbox",
        tag=tag,
        parent=parent,
        items=options,
        value=options[0] if options else "",
        callback=callback,
        width=width,
        height=max(2, int(num_items)) * 22,
        extra={"num_items": num_items, **kwargs},
    ).tag


def add_text(
    text: str = "",
    tag: str | None = None,
    color: Any = None,
    wrap: Any = None,
    parent: Any = None,
    show: bool = True,
    **kwargs: Any,
) -> str:
    return _create(
        "text",
        tag=tag,
        parent=parent,
        value=text,
        label=text,
        color=color,
        show=show,
        extra={"wrap": wrap, **kwargs},
        payload_type=str(kwargs.get("payload_type") or ""),
        drop_callback=kwargs.get("drop_callback"),
    ).tag


def add_input_int(
    tag: str | None = None,
    default_value: int = 0,
    callback: Callable[..., Any] | None = None,
    width: Any = None,
    min_value: int | None = None,
    max_value: int | None = None,
    parent: Any = None,
    **kwargs: Any,
) -> str:
    return _create(
        "input_int",
        tag=tag,
        parent=parent,
        value=int(default_value),
        callback=callback,
        width=width,
        extra={"min": min_value, "max": max_value, **kwargs},
    ).tag


def add_separator(parent: Any = None, **kwargs: Any) -> str:
    return _create("separator", parent=parent, extra=dict(kwargs)).tag


def add_spacer(width: Any = None, height: Any = None, parent: Any = None, **kwargs: Any) -> str:
    return _create(
        "spacer", parent=parent, width=width, height=height, extra=dict(kwargs)
    ).tag


def add_image_button(
    source: Any = None,
    width: int = 48,
    height: int = 48,
    tag: str | None = None,
    callback: Callable[..., Any] | None = None,
    parent: Any = None,
    **kwargs: Any,
) -> str:
    return _create(
        "image_button",
        tag=tag,
        parent=parent,
        width=width,
        height=height,
        callback=callback,
        extra={"source": source, **kwargs},
    ).tag


def draw_circle(
    center: tuple[float, float] = (8, 8),
    radius: float = 6,
    fill: Any = None,
    color: Any = None,
    tag: str | None = None,
    parent: Any = None,
    **kwargs: Any,
) -> str:
    return _create(
        "lamp",
        tag=tag,
        parent=parent or _parent_of(),
        color=fill or color,
        extra={"center": center, "radius": radius, "fill": fill or color, **kwargs},
    ).tag


def draw_rectangle(
    pmin: tuple[float, float],
    pmax: tuple[float, float],
    parent: Any = None,
    fill: Any = None,
    color: Any = None,
    thickness: float = 1.0,
    tag: str | None = None,
    **kwargs: Any,
) -> str:
    return _create(
        "rect",
        tag=tag,
        parent=parent or _parent_of(),
        color=color,
        extra={
            "pmin": pmin,
            "pmax": pmax,
            "fill": fill,
            "thickness": thickness,
            **kwargs,
        },
        pos=[float(pmin[0]), float(pmin[1])],
        width=abs(float(pmax[0]) - float(pmin[0])),
        height=abs(float(pmax[1]) - float(pmin[1])),
    ).tag


def draw_line(
    p1: tuple[float, float],
    p2: tuple[float, float],
    parent: Any = None,
    color: Any = None,
    thickness: float = 1.0,
    tag: str | None = None,
    **kwargs: Any,
) -> str:
    return _create(
        "line",
        tag=tag,
        parent=parent or _parent_of(),
        color=color,
        extra={"p1": p1, "p2": p2, "thickness": thickness, **kwargs},
    ).tag


def draw_text(
    pos: tuple[float, float],
    text: str,
    parent: Any = None,
    size: float = 12,
    color: Any = None,
    tag: str | None = None,
    **kwargs: Any,
) -> str:
    return _create(
        "draw_text",
        tag=tag,
        parent=parent or _parent_of(),
        value=text,
        label=text,
        color=color,
        pos=[float(pos[0]), float(pos[1])],
        extra={"size": size, **kwargs},
    ).tag


def add_mouse_click_handler(
    button: Any = None,
    parent: Any = None,
    callback: Callable[..., Any] | None = None,
    **kwargs: Any,
) -> str:
    return _create(
        "mouse",
        parent=parent,
        callback=callback,
        extra={"button": button, "kind": "click", **kwargs},
    ).tag


def add_mouse_double_click_handler(
    button: Any = None,
    parent: Any = None,
    callback: Callable[..., Any] | None = None,
    **kwargs: Any,
) -> str:
    return _create(
        "mouse",
        parent=parent,
        callback=callback,
        extra={"button": button, "kind": "double", **kwargs},
    ).tag


def add_mouse_drag_handler(
    button: Any = None,
    threshold: float = 1.0,
    parent: Any = None,
    callback: Callable[..., Any] | None = None,
    **kwargs: Any,
) -> str:
    return _create(
        "mouse",
        parent=parent,
        callback=callback,
        extra={"button": button, "kind": "drag", "threshold": threshold, **kwargs},
    ).tag


def add_mouse_release_handler(
    button: Any = None,
    parent: Any = None,
    callback: Callable[..., Any] | None = None,
    **kwargs: Any,
) -> str:
    return _create(
        "mouse",
        parent=parent,
        callback=callback,
        extra={"button": button, "kind": "release", **kwargs},
    ).tag


def add_mouse_wheel_handler(
    parent: Any = None,
    callback: Callable[..., Any] | None = None,
    **kwargs: Any,
) -> str:
    return _create(
        "mouse",
        parent=parent,
        callback=callback,
        extra={"kind": "wheel", **kwargs},
    ).tag


def add_key_press_handler(callback: Callable[..., Any] | None = None, **kwargs: Any) -> str:
    return _create("key", callback=callback, extra=dict(kwargs)).tag


def dump_tree() -> str:
    """HTML-ish snapshot of the widget registry (harness screenshots)."""
    lines = ["<html><body><pre>MegaDesk host snapshot"]
    for tag, widget in _WIDGETS.items():
        lines.append(
            f"{tag} kind={widget.kind} value={widget.value!r} "
            f"label={widget.label!r} items={widget.items!r} "
            f"show={widget.show} enabled={widget.enabled} "
            f"w={widget.width} h={widget.height}"
        )
    lines.append("</pre></body></html>")
    return "\n".join(lines)


def _choice_visual(widget: Widget) -> tuple[list[Any], Any]:
    """Options plus a value NiceGUI's select will accept (or None)."""
    options = list(widget.items)
    value = widget.value
    if value in options:
        return options, value
    if value is not None and str(value) in {str(item) for item in options}:
        return options, str(value)
    return options, None


def css_color(color: Any) -> str:
    if color is None:
        return ""
    if isinstance(color, str):
        return color
    try:
        r, g, b = int(color[0]), int(color[1]), int(color[2])
        a = float(color[3]) / 255.0 if len(color) > 3 else 1.0
        return f"rgba({r},{g},{b},{a:.3f})"
    except Exception:
        return ""


def _image_src(src: Any) -> Any:
    """Turn a local icon path into something NiceGUI can render."""
    if not src:
        return None
    if str(src) in _WIDGETS:
        stored = _WIDGETS[str(src)].extra.get("path")
        if stored:
            src = stored
    try:
        path = Path(src)
        if path.is_file():
            mime = mimetypes.guess_type(path.name)[0] or "image/png"
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            return f"data:{mime};base64,{encoded}"
    except Exception:
        pass
    return src


def _parent_element(widget: Widget) -> Any:
    if not widget.parent:
        return None
    parent = _WIDGETS.get(widget.parent)
    return parent.element if parent is not None else None


def _on_click(widget: Widget) -> Callable[[], None]:
    def _handle() -> None:
        invoke(widget.callback, widget.tag, None, widget.user_data)

    return _handle


def _on_value(widget: Widget) -> Callable[[Any], None]:
    def _handle(event: Any = None) -> None:
        value = getattr(event, "value", event)
        widget.value = value
        if widget.extra.get("on_enter"):
            return
        invoke(widget.callback, widget.tag, value, widget.user_data)

    return _handle


def _attach_visual(widget: Widget) -> None:
    ui = _ui()
    if ui is None or widget.element is not None:
        return
    parent_el = _parent_element(widget)
    if parent_el is None and widget.kind not in {"window", "theme", "handlers", "key"}:
        return
    try:
        _build_element(ui, widget, parent_el)
    except Exception:
        log.exception("visual attach failed for %s kind=%s", widget.tag, widget.kind)
        widget.element = None


def _build_element(ui: Any, widget: Widget, parent_el: Any) -> None:
    kind = widget.kind

    def _enter() -> Any:
        return parent_el

    ctx = parent_el
    if ctx is None:
        return

    with ctx:
        if kind in {"column", "group"}:
            widget.element = ui.column().classes("md-col gap-1")
        elif kind == "row":
            widget.element = ui.row().classes("md-row items-center gap-1 no-wrap")
        elif kind == "child":
            height = widget.height if isinstance(widget.height, (int, float)) and widget.height > 0 else None
            el = ui.column().classes("md-child gap-1")
            if widget.extra.get("border", True):
                el.classes("md-border")
            style = []
            if height:
                style.append(f"height:{int(height)}px")
                style.append("overflow:auto")
            if widget.width not in (None, -1):
                try:
                    style.append(f"width:{int(widget.width)}px")
                except (TypeError, ValueError):
                    pass
            if style:
                el.style(";".join(style))
            widget.element = el
        elif kind == "button":
            btn = ui.button(widget.label or "", on_click=_on_click(widget))
            btn.props("dense unelevated size=sm no-caps")
            if not widget.enabled:
                btn.disable()
            if widget.width not in (None, -1):
                try:
                    btn.style(f"min-width:{int(widget.width)}px")
                except (TypeError, ValueError):
                    pass
            widget.element = btn
        elif kind == "input":
            inp = ui.input(
                value=str(widget.value or ""),
                placeholder=str(widget.extra.get("hint") or ""),
                on_change=_on_value(widget),
            ).props("dense outlined")
            if widget.extra.get("readonly"):
                inp.props("readonly")
            if widget.extra.get("on_enter") and widget.callback:
                inp.on(
                    "keydown.enter",
                    lambda _e=None, w=widget: invoke(
                        w.callback, w.tag, w.value, w.user_data
                    ),
                )
            widget.element = inp
        elif kind == "textarea":
            area = ui.textarea(
                value=str(widget.value or ""),
                placeholder=str(widget.extra.get("hint") or ""),
                on_change=_on_value(widget),
            ).props("dense outlined")
            if widget.extra.get("readonly"):
                area.props("readonly")
            if widget.height not in (None, -1):
                try:
                    area.style(f"height:{int(widget.height)}px")
                except (TypeError, ValueError):
                    pass
            widget.element = area
        elif kind == "combo":
            options, value = _choice_visual(widget)
            sel = ui.select(
                options=options,
                value=value,
                on_change=_on_value(widget),
            ).props("dense outlined")
            if widget.width not in (None, -1):
                try:
                    sel.style(f"min-width:{int(widget.width)}px")
                except (TypeError, ValueError):
                    pass
            widget.element = sel
        elif kind == "listbox":
            options, value = _choice_visual(widget)
            sel = ui.select(
                options=options,
                value=value,
                on_change=_on_value(widget),
            ).props("dense outlined")
            widget.element = sel
        elif kind == "text":
            parent_w = _WIDGETS.get(widget.parent or "")
            if parent_w is not None and parent_w.kind == "tooltip":
                host_el = parent_w.extra.get("tooltip_host")
                if host_el is not None:
                    try:
                        host_el.tooltip(str(widget.value or widget.label or ""))
                    except Exception:
                        pass
                widget.element = ui.element("span").style("display:none")
            else:
                label = ui.label(str(widget.value or ""))
                label.classes("text-caption")
                css = css_color(widget.color)
                if css:
                    label.style(f"color:{css}")
                widget.element = label
        elif kind == "input_int":
            num = ui.number(
                value=widget.value,
                min=widget.extra.get("min"),
                max=widget.extra.get("max"),
                on_change=_on_value(widget),
            ).props("dense outlined")
            widget.element = num
        elif kind == "separator":
            widget.element = ui.separator()
        elif kind == "spacer":
            el = ui.element("div")
            bits = []
            if widget.width:
                bits.append(f"width:{int(widget.width)}px")
            if widget.height:
                bits.append(f"height:{int(widget.height)}px")
            if bits:
                el.style(";".join(bits))
            widget.element = el
        elif kind == "lamp":
            el = ui.element("div").classes("md-lamp")
            css = css_color(widget.extra.get("fill") or widget.color)
            if css:
                el.style(f"background:{css}")
            widget.element = el
        elif kind == "drawlist":
            el = ui.element("div").classes("md-draw")
            style = ["position:relative", "overflow:hidden"]
            if widget.width:
                style.append(f"width:{int(widget.width)}px")
            if widget.height:
                style.append(f"height:{int(widget.height)}px")
            el.style(";".join(style))
            widget.element = el
        elif kind == "rect":
            el = ui.element("div").classes("md-shape")
            fill = css_color(widget.extra.get("fill"))
            edge = css_color(widget.color) or "#333"
            x, y = widget.extra.get("pmin", (0, 0))
            el.style(
                f"position:absolute;left:{x}px;top:{y}px;"
                f"width:{widget.width}px;height:{widget.height}px;"
                f"background:{fill or 'transparent'};border:1px solid {edge};"
            )
            widget.element = el
        elif kind == "draw_text":
            el = ui.label(str(widget.value or ""))
            x, y = widget.pos
            size = widget.extra.get("size") or 12
            css = css_color(widget.color)
            el.style(
                f"position:absolute;left:{x}px;top:{y}px;font-size:{size}px;"
                + (f"color:{css};" if css else "")
            )
            widget.element = el
        elif kind == "line":
            widget.element = ui.element("div")
        elif kind == "tab_bar":
            tabs = ui.tabs().classes("md-tabs")
            panels = ui.tab_panels(tabs).classes("w-full")
            widget.element = panels
            widget.extra["tabs_el"] = tabs
            widget.extra["panels_el"] = panels
        elif kind == "tab":
            bar = _WIDGETS.get(widget.parent or "")
            tabs_el = bar.extra.get("tabs_el") if bar is not None else None
            panels_el = bar.extra.get("panels_el") if bar is not None else None
            if tabs_el is None or panels_el is None:
                widget.element = ui.column()
                return
            with tabs_el:
                tab_el = ui.tab(widget.label or widget.tag)
            with panels_el:
                panel = ui.tab_panel(tab_el)
            widget.element = panel
            widget.extra["tab_el"] = tab_el
            if bar is not None and bar.value == widget.tag:
                try:
                    panels_el.value = tab_el
                    tabs_el.value = tab_el
                except Exception:
                    pass
        elif kind == "node":
            card = ui.card().classes("md-node")
            x, y = widget.pos
            card.style(f"left:{x}px;top:{y}px")
            if widget.extra.get("selected"):
                card.classes("md-node-selected")
            widget.element = card
        elif kind == "editor":
            board = ui.element("div").classes("md-board")
            widget.element = board
        elif kind == "image_button":
            src = _image_src(widget.extra.get("source"))
            col = ui.column().classes("items-center cursor-pointer")
            size = f"width:{int(widget.width or 48)}px;height:{int(widget.height or 48)}px"
            if src:
                ui.image(src).style(size)
            else:
                ui.element("div").style(f"{size};background:#111")
            col.on("click", _on_click(widget))
            widget.element = col
        elif kind in {"payload", "tooltip", "file_dialog", "handlers", "mouse", "key", "theme"}:
            hidden = ui.element("div").style("display:none")
            if kind == "tooltip":
                parent_w = _WIDGETS.get(widget.parent or "")
                hidden_host = parent_w.element if parent_w is not None else parent_el
                widget.extra["tooltip_host"] = hidden_host
            widget.element = hidden
        elif kind == "window":
            widget.element = ui.column().classes("md-root w-full")
        else:
            widget.element = ui.element("div")

    classes = {
        "canvas_body": "md-body w-full",
        "graph_bar": "md-bar w-full",
        "catalog_sidebar": "md-catalog",
        "graph_editor_host": "md-board-host",
        "graph_editor": "md-board",
        "supervisor_panel_window": "md-supervisor",
        "voice_deck_panel_window": "md-voice w-full",
    }.get(widget.tag)
    if classes and widget.element is not None:
        try:
            widget.element.classes(classes)
        except Exception:
            pass

    if not widget.show and widget.element is not None:
        try:
            widget.element.set_visibility(False)
        except Exception:
            pass


def _sync_visual(widget: Widget) -> None:
    el = widget.element
    if el is None:
        return
    try:
        if widget.kind in {"combo", "listbox"}:
            options, value = _choice_visual(widget)
            try:
                el.options = options
            except Exception:
                pass
            el.value = value
        elif widget.kind in {"input", "textarea", "input_int"}:
            el.value = widget.value
        elif widget.kind == "text":
            el.set_text(str(widget.value or ""))
            css = css_color(widget.color)
            if css:
                el.style(f"color:{css}")
        elif widget.kind == "button":
            el.set_text(widget.label or "")
            if widget.enabled:
                el.enable()
            else:
                el.disable()
        elif widget.kind == "lamp":
            css = css_color(widget.extra.get("fill") or widget.color)
            if css:
                el.style(f"background:{css}")
        if widget.show:
            el.set_visibility(True)
        else:
            el.set_visibility(False)
        if widget.kind == "child" and isinstance(widget.height, (int, float)):
            el.style(f"height:{int(widget.height)}px;overflow:auto")
        if widget.kind == "node":
            el.style(f"left:{widget.pos[0]}px;top:{widget.pos[1]}px")
        if widget.kind == "tab_bar" and widget.value:
            tab = _WIDGETS.get(str(widget.value))
            tab_el = tab.extra.get("tab_el") if tab is not None else None
            panels = widget.extra.get("panels_el")
            tabs = widget.extra.get("tabs_el")
            if tab_el is not None:
                if panels is not None:
                    panels.value = tab_el
                if tabs is not None:
                    tabs.value = tab_el
    except Exception:
        pass


def bind_theme(_theme: Any = None) -> None:
    return


def get_item_rect_min(tag: Any) -> list[float]:
    return get_item_pos(tag)


def get_item_height(tag: Any) -> int:
    widget = _WIDGETS.get(str(tag))
    if widget is None:
        return 0
    try:
        return int(widget.height or 0)
    except (TypeError, ValueError):
        return 0


def get_focused_item() -> Optional[str]:
    return _FOCUSED


def get_item_type(tag: Any) -> str:
    widget = _WIDGETS.get(str(tag))
    if widget is None:
        return ""
    mapping = {
        "input": "mvAppItemType::InputText",
        "textarea": "mvAppItemType::InputText",
        "input_int": "mvAppItemType::InputInt",
    }
    return mapping.get(widget.kind, f"mvAppItemType::{widget.kind}")


def add_texture_registry(tag: str | None = None, **kwargs: Any) -> str:
    return _create("texture_registry", tag=tag, extra=dict(kwargs)).tag


def load_image(path: Any) -> tuple[int, int, int, list[float]]:
    global _LAST_IMAGE_PATH
    _LAST_IMAGE_PATH = path
    return (48, 48, 4, [])


def add_static_texture(
    width: int,
    height: int,
    data: Any = None,
    tag: str | None = None,
    parent: Any = None,
    **kwargs: Any,
) -> str:
    return _create(
        "texture",
        tag=tag,
        parent=parent,
        width=width,
        height=height,
        extra={"data": data, "path": _LAST_IMAGE_PATH, **kwargs},
    ).tag


def create_context() -> None:
    global _RUNNING, _FRAME, _RESIZE_CALLBACK
    reset()
    _FRAME_CALLBACKS.clear()
    _FRAME = 0
    _RESIZE_CALLBACK = None
    _RUNNING = True


def destroy_context() -> None:
    global _RUNNING, _RESIZE_CALLBACK
    _RUNNING = False
    _RESIZE_CALLBACK = None
    _FRAME_CALLBACKS.clear()
    reset()


def is_dearpygui_running() -> bool:
    return bool(_RUNNING)


def create_viewport(
    title: str = "",
    width: int = 1280,
    height: int = 800,
    x_pos: int = 0,
    y_pos: int = 0,
    **_kwargs: Any,
) -> None:
    _ = title, x_pos, y_pos
    set_viewport_size(width, height)


def setup_dearpygui() -> None:
    return


def show_viewport(*_args: Any, **_kwargs: Any) -> None:
    return


def set_primary_window(_tag: Any, _value: Any = True) -> None:
    return


def set_viewport_resize_callback(callback: Callable[..., Any] | None) -> None:
    global _RESIZE_CALLBACK
    _RESIZE_CALLBACK = callback


def set_frame_callback(frame: int, callback: Callable[..., Any]) -> None:
    _FRAME_CALLBACKS.setdefault(int(frame), []).append(callback)


def render_dearpygui_frame() -> None:
    global _FRAME
    if not _RUNNING:
        return
    _FRAME += 1
    for callback in list(_FRAME_CALLBACKS.pop(_FRAME, [])):
        try:
            callback()
        except Exception:
            pass


def output_frame_buffer(file: str = "", **_kwargs: Any) -> None:
    """Write a host widget snapshot for harness screenshots."""
    target = Path(file)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(dump_tree(), encoding="utf-8")
