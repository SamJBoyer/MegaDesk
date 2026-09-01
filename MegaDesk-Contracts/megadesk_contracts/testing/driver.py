"""Tag-based driver for one graph-hosted FE.

Every FE derives its widget tags from the ``tag_prefix`` the canvas host hands to
``FeSpec.build``, which is ``megadesk::{member_id}``. That makes each widget
addressable without reaching into the FE's module globals — a callback that gets
unwired by a refactor then fails the test instead of silently passing.
"""

from __future__ import annotations

import inspect
import re
from typing import Any, Callable, Optional

from megadesk_contracts import host as dpg


class WidgetMissing(AssertionError):
    """Raised when a test addresses a widget that does not exist."""


class CallbackMissing(AssertionError):
    """Raised when a widget exists but has no bound callback."""


def invoke_callback(
    callback: Callable[..., Any],
    sender: Any,
    app_data: Any,
    user_data: Any,
) -> Any:
    """Call a DPG callback with as many of (sender, app_data, user_data) as it takes.

    Dear PyGui adapts to the callback's arity, so FEs legitimately bind
    zero-argument lambdas alongside three-argument methods.
    """
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


class NodeDriver:
    """Read, write and click widgets of a single hosted node by tag suffix."""

    def __init__(
        self,
        harness: Any,
        member_id: str,
        node_name: str,
        *,
        tag_prefix: Optional[str] = None,
    ) -> None:
        self.harness = harness
        self.member_id = member_id
        self.node_name = node_name
        self._tag_prefix = tag_prefix

    def __repr__(self) -> str:
        return f"NodeDriver({self.node_name!r}, member_id={self.member_id!r})"

    # --- addressing ---

    @property
    def tag_prefix(self) -> str:
        """The prefix the canvas passes to ``FeSpec.build``, or a chrome panel tag."""
        if self._tag_prefix:
            return self._tag_prefix
        return f"megadesk::{self.member_id}"

    @property
    def content_tag(self) -> str:
        return f"{self.tag_prefix}::content"

    def tag(self, suffix: str) -> str:
        return f"{self.tag_prefix}::{suffix}"

    def exists(self, suffix: str) -> bool:
        return dpg.does_item_exist(self.tag(suffix))

    def require(self, suffix: str) -> str:
        tag = self.tag(suffix)
        if not dpg.does_item_exist(tag):
            raise WidgetMissing(
                f"{self.node_name}: no widget tagged {suffix!r} ({tag}). "
                f"Existing suffixes: {sorted(self.suffixes())}"
            )
        return tag

    def suffixes(self, pattern: str | None = None) -> list[str]:
        """Every tag suffix under this node, optionally filtered by regex.

        Useful when a test needs to discover dynamically keyed row widgets, and
        for failure messages that would otherwise just say "missing".
        """
        prefix = f"{self.tag_prefix}::"
        found: list[str] = []
        for alias in dpg.get_aliases():
            text = str(alias)
            if text.startswith(prefix):
                found.append(text[len(prefix) :])
        if pattern is not None:
            regex = re.compile(pattern)
            found = [s for s in found if regex.search(s)]
        return sorted(found)

    # --- state ---

    def get(self, suffix: str) -> Any:
        return dpg.get_value(self.require(suffix))

    def set(self, suffix: str, value: Any) -> None:
        dpg.set_value(self.require(suffix), value)

    def label(self, suffix: str) -> str:
        config = dpg.get_item_configuration(self.require(suffix))
        return str(config.get("label") or "")

    def user_data(self, suffix: str) -> Any:
        return dpg.get_item_user_data(self.require(suffix))

    def items(self, suffix: str) -> list[str]:
        """A combo's or listbox's options, in the order the user would see them."""
        config = dpg.get_item_configuration(self.require(suffix))
        return [str(item) for item in (config.get("items") or [])]

    def shown(self, suffix: str) -> bool:
        """The widget's configured ``show`` flag.

        Not ``is_item_visible``, which also depends on scroll position and
        whether the parent was drawn this frame.
        """
        config = dpg.get_item_configuration(self.require(suffix))
        return bool(config.get("show"))

    def enabled(self, suffix: str) -> bool:
        config = dpg.get_item_configuration(self.require(suffix))
        return bool(config.get("enabled"))

    # --- interaction ---

    def fire(self, suffix: str, app_data: Any = None) -> Any:
        """Invoke the widget's real bound callback."""
        tag = self.require(suffix)
        callback = dpg.get_item_callback(tag)
        if callback is None:
            raise CallbackMissing(
                f"{self.node_name}: widget {suffix!r} has no callback bound"
            )
        return invoke_callback(callback, tag, app_data, dpg.get_item_user_data(tag))

    def drop(self, suffix: str, app_data: Any = None) -> Any:
        """Complete a drag-and-drop onto a widget: invoke its drop_callback."""
        tag = self.require(suffix)
        callback = dpg.get_item_drop_callback(tag)
        if callback is None:
            raise CallbackMissing(
                f"{self.node_name}: widget {suffix!r} has no drop_callback bound"
            )
        return invoke_callback(callback, tag, app_data, dpg.get_item_user_data(tag))

    def click(self, suffix: str) -> Any:
        """Press a button: its callback receives ``app_data=None``, as DPG does."""
        return self.fire(suffix, app_data=None)

    def type_into(self, suffix: str, text: str) -> Any:
        """Set an input's value and fire its callback, like typing + Enter."""
        self.set(suffix, text)
        return self.fire(suffix, app_data=text)

    def check(self, suffix: str, value: bool = True) -> Any:
        """Tick or untick a checkbox, firing its callback as a real click does."""
        self.set(suffix, bool(value))
        return self.fire(suffix, app_data=bool(value))

    def select(self, suffix: str, value: str) -> Any:
        """Pick a combo/listbox value, firing the callback only if one is bound."""
        self.set(suffix, value)
        tag = self.tag(suffix)
        if dpg.get_item_callback(tag) is None:
            return None
        return self.fire(suffix, app_data=value)

    # --- lifecycle ---

    def is_hosted(self) -> bool:
        return dpg.does_item_exist(self.tag_prefix)

    def close(self) -> None:
        """Press the host node's close button and let the engine process it."""
        tag = f"{self.tag_prefix}::close"
        if not dpg.does_item_exist(tag):
            raise WidgetMissing(f"{self.node_name}: no close button at {tag}")
        callback = dpg.get_item_callback(tag)
        if callback is None:
            raise CallbackMissing(f"{self.node_name}: close button has no callback")
        invoke_callback(callback, tag, None, dpg.get_item_user_data(tag))
        self.harness.pump(2)

    # --- FE instance escape hatch (avoid in assertions) ---

    def live_instance(self, live_map: dict[str, Any]) -> Optional[Any]:
        """Look this node's FE object up in a module ``_LIVE`` map.

        Only for arranging state a GUI cannot reach (there is no widget for it).
        Never assert through this: module state stays consistent even when the
        widget wiring that production depends on is broken.
        """
        return live_map.get(self.tag_prefix)
