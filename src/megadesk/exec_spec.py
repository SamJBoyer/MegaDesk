"""Thin FE/BE launch specs for MegaDesk nodes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

Mode = Literal["FE", "BE"]


@dataclass(frozen=True)
class FeSpec:
    """Front-end description for MegaDesk canvas hosting.

    ``build`` must create a *hosted content panel* for the canvas shell:

        build(
            tag,
            *,
            pos=None,
            on_close=None,
            width=…,
            height=…,
            no_move=True,
            no_resize=True,
            no_title_bar=True,
        ) -> window_tag

    MegaDesk owns frame chrome, selection, drag, and resize. The window is a
    fixed content panel glued to the member's world position via push sync.
    """

    name: str
    description: str
    icon: str | None
    default_width: int
    default_height: int
    build: Callable[..., str]


@dataclass(frozen=True)
class BeSpec:
    """Back-end launch instruction for Supervisor subprocess management."""

    name: str
    argv: list[str]
    cwd: str | None = None
