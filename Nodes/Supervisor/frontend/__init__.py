"""Supervisor operator frontend — MegaDesk FeSpec build() target.

Keep this package init free of Dear PyGui imports so BE-only installs work.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

__all__ = ["build_ui"]

if TYPE_CHECKING:
    from frontend.app import build_ui as build_ui


def __getattr__(name: str) -> Any:
    if name == "build_ui":
        from frontend.app import build_ui as _build_ui

        return _build_ui
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
