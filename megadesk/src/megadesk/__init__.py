"""Shared MegaDesk node contract: FeSpec / BeSpec and entry-point discovery."""

from megadesk.discovery import (
    ENTRY_POINT_GROUP,
    discover_backends,
    discover_frontends,
    get_backend,
    has_backend,
    load_exec_spec,
)
from megadesk.exec_spec import BeSpec, FeSpec, Mode
from megadesk import frame_pump
from megadesk.supervisor_client import SupervisorClient

__all__ = [
    "ENTRY_POINT_GROUP",
    "BeSpec",
    "FeSpec",
    "Mode",
    "SupervisorClient",
    "discover_backends",
    "discover_frontends",
    "frame_pump",
    "get_backend",
    "has_backend",
    "load_exec_spec",
]
