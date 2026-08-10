"""Shared MegaDesk node contract: FeSpec / BeSpec and entry-point discovery."""

from megadesk_contracts.discovery import (
    ENTRY_POINT_GROUP,
    discover_backends,
    discover_frontends,
    get_backend,
    has_backend,
    load_exec_spec,
)
from megadesk_contracts.exec_spec import BeSpec, FeSpec, Mode
from megadesk_contracts import frame_pump
from megadesk_contracts.node_logging import configure_node_logging
from megadesk_contracts.supervisor_client import (
    REDIS_DB_EPHEMERAL,
    REDIS_DB_PERSISTENT,
    SUPERVISOR_ALIVE_KEY,
    SUPERVISOR_NODE_NAME,
    SUPERVISOR_SINGLETON_KEY,
    SupervisorClient,
    ensure_supervisor_running,
)

__all__ = [
    "ENTRY_POINT_GROUP",
    "BeSpec",
    "FeSpec",
    "Mode",
    "REDIS_DB_EPHEMERAL",
    "REDIS_DB_PERSISTENT",
    "SUPERVISOR_ALIVE_KEY",
    "SUPERVISOR_NODE_NAME",
    "SUPERVISOR_SINGLETON_KEY",
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
