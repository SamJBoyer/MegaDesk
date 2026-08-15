"""Thin FE/BE launch specs for MegaDesk nodes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

Mode = Literal["FE", "BE"]


@dataclass(frozen=True)
class FeSpec:
    """Front-end description for MegaDesk canvas hosting.

    ``build`` fills a host-owned content parent (never creates its own window):

        build(parent, *, tag_prefix, width=…, height=…) -> None

    MegaDesk owns the shell (header, close, position, size). The FE only adds
    widgets under ``parent``. Store cleanup on the parent with
    ``dpg.set_item_user_data(parent, cleanup_fn)``.

    ``backends`` is the set of Supervisor ``node_endpoint`` names the canvas
    ``XADD``s to ``LAUNCHREQUEST`` when this FE is hosted (drop or canvas open).
    Empty means this FE does not start a BE.
    """

    name: str
    description: str
    icon: str | None
    default_width: int
    default_height: int
    build: Callable[..., None]
    backends: tuple[str, ...] = ()


@dataclass(frozen=True)
class BeSpec:
    """Back-end launch instruction for Supervisor subprocess management."""

    name: str
    argv: list[str]
    cwd: str | None = None
