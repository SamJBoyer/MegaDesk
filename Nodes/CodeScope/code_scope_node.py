"""MegaDesk.nodes entry point for CodeScope (FE + BE).

FE: Dear PyGui repo intake and question box (requires ``[canvas]``).
BE: CodeScopeManager, which answers CODEQ:ASK from a warm local Cursor agent.
"""

from __future__ import annotations

import sys
from pathlib import Path

from megadesk_contracts import BeSpec, FeSpec, Mode

_CODE_SCOPE_ROOT = Path(__file__).resolve().parent
NODE_NAME = "code_scope"
_ICON = str(_CODE_SCOPE_ROOT / "Etc" / "Artwork" / "icon.png")


def get_exec_spec(mode: Mode) -> FeSpec | BeSpec | None:
    if mode == "FE":
        from code_scope_frontend.app import build_ui

        icon = _ICON if Path(_ICON).is_file() else None
        return FeSpec(
            name=NODE_NAME,
            description="Ask questions about a cloned repository.",
            icon=icon,
            default_width=520,
            default_height=240,
            build=build_ui,
        )
    if mode == "BE":
        return BeSpec(
            name=NODE_NAME,
            argv=[sys.executable, "-u", "-m", "CodeScopeManager"],
            cwd=str(_CODE_SCOPE_ROOT),
        )
    return None
