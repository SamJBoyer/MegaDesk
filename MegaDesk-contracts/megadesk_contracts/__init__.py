"""Shared MegaDesk node contract: FeSpec / BeSpec and entry-point discovery."""

from megadesk_contracts.agent_errors import (
    AgentError,
    AgentRunError,
    AgentStartupError,
)
from megadesk_contracts.cloud_runtime import CloudLaunch, CloudRuntime, CloudStatus
from megadesk_contracts.discovery import (
    ENTRY_POINT_GROUP,
    discover_backends,
    discover_frontends,
    get_backend,
    has_backend,
    load_exec_spec,
)
from megadesk_contracts.exec_spec import BeSpec, FeSpec, Mode
from megadesk_contracts import frame_pump, wire
from megadesk_contracts.node_logging import configure_node_logging
from megadesk_contracts.realtime import RealtimeEvent, RealtimeTransport
from megadesk_contracts.repo import (
    CloneError,
    clone_path,
    default_scope_root,
    ensure_clone,
    is_clone,
    refresh_clone,
    repo_name_from_url,
)
from megadesk_contracts.supervisor_client import (
    DEFAULT_REDIS_URL,
    REDIS_DB_EPHEMERAL,
    REDIS_DB_PERSISTENT,
    SUPERVISOR_ALIVE_KEY,
    SUPERVISOR_NODE_NAME,
    SUPERVISOR_SINGLETON_KEY,
    SupervisorClient,
    ensure_supervisor_running,
    resolve_redis_url,
)

__all__ = [
    "ENTRY_POINT_GROUP",
    "AgentError",
    "AgentRunError",
    "AgentStartupError",
    "BeSpec",
    "CloneError",
    "CloudLaunch",
    "CloudRuntime",
    "CloudStatus",
    "FeSpec",
    "Mode",
    "RealtimeEvent",
    "RealtimeTransport",
    "DEFAULT_REDIS_URL",
    "REDIS_DB_EPHEMERAL",
    "REDIS_DB_PERSISTENT",
    "SUPERVISOR_ALIVE_KEY",
    "SUPERVISOR_NODE_NAME",
    "SUPERVISOR_SINGLETON_KEY",
    "SupervisorClient",
    "clone_path",
    "configure_node_logging",
    "default_scope_root",
    "discover_backends",
    "discover_frontends",
    "ensure_clone",
    "ensure_supervisor_running",
    "frame_pump",
    "get_backend",
    "has_backend",
    "is_clone",
    "load_exec_spec",
    "refresh_clone",
    "repo_name_from_url",
    "resolve_redis_url",
    "wire",
]
