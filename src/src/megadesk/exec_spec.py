"""Thin FE/BE launch specs for MegaDesk nodes (not BaseNode)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

Mode = Literal["FE", "BE"]


@dataclass(frozen=True)
class FeSpec:
    """Front-end description for MegaDesk canvas hosting."""

    name: str
    description: str
    icon: str | None
    default_width: int
    default_height: int
    build: Callable[..., str]  # (tag, *, pos, on_close=None) -> window_tag


@dataclass(frozen=True)
class BeSpec:
    """Back-end launch instruction for Supervisor subprocess management."""

    name: str
    argv: list[str]
    cwd: str | None = None
