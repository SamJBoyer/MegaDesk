"""MegaDesk.nodes entry point for CodeScope — a cloud node.

FE: Dear PyGui repo intake and question box (requires ``[canvas]``).
The process that clones and answers runs elsewhere (``CODESCOPE_URL``).
There is no Supervisor-launched BE on this machine.
"""

from __future__ import annotations

from pathlib import Path

from megadesk_contracts import KIND_CLOUD, FeSpec, ToolSpec

_CODE_SCOPE_ROOT = Path(__file__).resolve().parent
NODE_NAME = "code_scope"
_ICON = str(_CODE_SCOPE_ROOT / "Etc" / "Artwork" / "icon.png")


def get_fe_spec() -> FeSpec | None:
    from code_scope_frontend.app import build_ui

    icon = _ICON if Path(_ICON).is_file() else None
    return FeSpec(
        name=NODE_NAME,
        description="Ask questions about a cloned repository.",
        icon=icon,
        default_width=520,
        default_height=240,
        build=build_ui,
        kind=KIND_CLOUD,
    )


def get_be_spec():
    return None


def get_tool_spec() -> ToolSpec | None:
    from code_scope_tools import tool_spec

    return tool_spec()
