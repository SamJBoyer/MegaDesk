"""Assemble the voice catalog from discovered node ToolSpecs.

The schemas and instructions live in each node's tool folder. This module is
the one place the realtime session asks what the model may do: it discovers
``get_tool_spec()`` the same way the canvas discovers ``get_fe_spec()``, and
falls back to importing in-tree node modules so tests against this worktree
still see tools when an editable install is stale.
"""

from __future__ import annotations

import importlib
from typing import Callable

from megadesk_contracts import ToolSpec, compose_tool_specs, discover_tools

from code_scope_tools import (
    ANSWER_PREFIX,
    TOOL_ASK_CODEBASE,
    TOOL_DISPATCH_DOC_AGENT,
    TOOL_SET_REPO,
)
from voice_deck_tools import TOOL_END_SESSION, is_farewell
from work_dispatcher_tools import (
    TOOL_CHOOSE_TICKET,
    TOOL_LIST_TICKETS,
    TOOL_SEND_TICKET,
    TOOL_SET_DISPATCH,
)

__all__ = [
    "ANSWER_PREFIX",
    "TOOL_ASK_CODEBASE",
    "TOOL_CHOOSE_TICKET",
    "TOOL_DISPATCH_DOC_AGENT",
    "TOOL_END_SESSION",
    "TOOL_LIST_TICKETS",
    "TOOL_SEND_TICKET",
    "TOOL_SET_DISPATCH",
    "TOOL_SET_REPO",
    "collected_specs",
    "is_farewell",
    "session_instructions",
    "tool_handlers",
    "tool_schemas",
]

_IN_TREE_NODES = (
    "voice_deck_node",
    "code_scope_node",
    "work_dispatcher_node",
    "notepad_node",
)


def collected_specs() -> list[ToolSpec]:
    """Every ToolSpec VoiceDeck should hand the model."""
    by_name = dict(discover_tools())
    for mod_name in _IN_TREE_NODES:
        try:
            mod = importlib.import_module(mod_name)
        except ImportError:
            continue
        fn = getattr(mod, "get_tool_spec", None)
        if not callable(fn):
            continue
        spec = fn()
        if isinstance(spec, ToolSpec):
            by_name.setdefault(spec.name, spec)
    return list(by_name.values())


def _catalog() -> tuple[str, list[dict], dict[str, Callable[..., dict]]]:
    return compose_tool_specs(collected_specs())


def session_instructions() -> str:
    """Combined prompt: VoiceDeck hang-up rules, then each node's tools."""
    return _catalog()[0]


def tool_schemas() -> list[dict]:
    """Realtime ``session.tools`` entries from every discovered ToolSpec."""
    return _catalog()[1]


def tool_handlers() -> dict[str, Callable[..., dict]]:
    """Tool name → ``(arguments, host) -> dict``."""
    return _catalog()[2]


def __getattr__(name: str):
    if name == "INSTRUCTIONS":
        return session_instructions()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
