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
from megadesk.node_logging import configure_node_logging
from megadesk.supervisor_client import (
    SUPERVISOR_NODE_NAME,
    SupervisorClient,
    ensure_supervisor_running,
)

__all__ = [
    "ENTRY_POINT_GROUP",
    "BeSpec",
    "FeSpec",
    "Mode",
    "SUPERVISOR_NODE_NAME",
    "SupervisorClient",
    "configure_node_logging",
    "discover_backends",
    "discover_frontends",
    "ensure_supervisor_running",
    "frame_pump",
    "get_backend",
    "has_backend",
    "load_exec_spec",
]
