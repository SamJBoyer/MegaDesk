"""Thin FE/BE launch specs for MegaDesk nodes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal, Mapping

Mode = Literal["FE", "BE"]


@dataclass(frozen=True)
class FeSpec:
    """Front-end description for MegaDesk graph hosting.

    ``build`` fills a host-owned content parent (never creates its own window):

        build(parent, *, tag_prefix, width=…, height=…) -> None

    MegaDesk owns the shell (header, close, position, size). The FE only adds
    widgets under ``parent``. Store cleanup on the parent with
    ``dpg.set_item_user_data(parent, cleanup_fn)``.

    ``backends`` is the set of Supervisor ``node_endpoint`` names the canvas
    ``XADD``s to ``LAUNCHREQUEST`` when this FE is hosted (drop or graph open).
    Empty means this FE does not start a BE.

    Parameters (see ``megadesk_contracts.parameters``): ``get_fe_spec`` receives
    the values a graph saved for this member and returns a spec that already has
    them folded in — ``build`` closes over them, ``backend_parameters`` carries
    whichever subset the BE needs on ``LAUNCHREQUEST``, and ``parameters``
    declares the names this node recognizes (usually straight from its
    ``parameters.yaml``). ``read_parameters`` reads current values back out of a
    live instance, given the ``tag_prefix`` the host built it with, so the graph
    bar can capture what the operator typed.
    """

    name: str
    description: str
    icon: str | None
    default_width: int
    default_height: int
    build: Callable[..., None]
    backends: tuple[str, ...] = ()
    parameters: tuple[str, ...] = ()
    backend_parameters: Mapping[str, str] = field(default_factory=dict)
    read_parameters: Callable[[str], Mapping[str, str]] | None = None


@dataclass(frozen=True)
class BeSpec:
    """Back-end launch instruction for Supervisor subprocess management."""

    name: str
    argv: list[str]
    cwd: str | None = None
