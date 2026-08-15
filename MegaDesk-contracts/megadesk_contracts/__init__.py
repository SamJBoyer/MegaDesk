"""Shared MegaDesk node contract: FeSpec / BeSpec and entry-point discovery."""

from megadesk_contracts.agent_errors import (
    AgentError,
    AgentRunError,
    AgentStartupError,
)
from megadesk_contracts.cloud_runtime import CloudLaunch, CloudRuntime, CloudStatus
from megadesk_contracts.discovery import (
    ENTRY_POINT_GROUP,
    backends_for_frontend,
    discover_backends,
    discover_frontends,
    get_backend,
    has_backend,
    load_exec_spec,
)
from megadesk_contracts.exec_spec import BeSpec, FeSpec, Mode
from megadesk_contracts import frame_pump, wire
from megadesk_contracts.node_logging import configure_node_logging
from megadesk_contracts.node_runtime import (
    HEARTBEAT_INTERVAL_SEC,
    NODE_HEARTBEAT_PREFIX,
    NODE_SHUTDOWN_KEY,
    NodeRuntime,
    clear_shutdown,
    heartbeat_key,
    is_reported_node_alive,
    node_should_stop,
    pid_is_alive,
    request_shutdown,
    shutdown_key,
)
from megadesk_contracts.paths import ENV_CANVAS_ROOT, resolve_canvas_root, resolve_logs_root
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
    "backends_for_frontend",
    "clear_shutdown",
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
    "heartbeat_key",
    "is_clone",
    "is_reported_node_alive",
    "load_exec_spec",
    "node_should_stop",
    "pid_is_alive",
    "refresh_clone",
    "repo_name_from_url",
    "request_shutdown",
    "resolve_canvas_root",
    "resolve_logs_root",
    "resolve_redis_url",
    "shutdown_key",
    "wire",
    "ENV_CANVAS_ROOT",
    "HEARTBEAT_INTERVAL_SEC",
    "NODE_HEARTBEAT_PREFIX",
    "NODE_SHUTDOWN_KEY",
    "NodeRuntime",
]
