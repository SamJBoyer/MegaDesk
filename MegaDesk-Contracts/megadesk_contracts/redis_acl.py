"""Redis ACL for MachineFactory sandbox IPC on the host pair.

The sandbox authenticates as ``FACTORY_ACL_USER`` and may touch factory keys
only. Host MegaDesk (canvas, supervisor, MachineFactory manager) keeps the
default/admin user.

If ACL cannot be applied, sandbox launch must fail rather than inject an
unauthenticated admin URL.
"""

from __future__ import annotations

import os
import secrets
from typing import Optional

from megadesk_contracts.supervisor_client import (
    redis_connect,
    redis_url_with_auth,
    resolve_ephemeral_db,
    resolve_redis_url,
)
from megadesk_contracts.wire.graph import GRAPHEVENT_STREAM, GRAPHRUN_PREFIX
from megadesk_contracts.wire.machine import (
    AGENTHANDLER_PREFIX,
    FINISHED_PREFIX,
    WORKORDER_STREAM,
)

FACTORY_ACL_USER = "megadesk-factory"
ENV_FACTORY_ACL_PASSWORD = "MEGADESK_FACTORY_REDIS_PASSWORD"

# Keys the sandbox already uses. Names come from wire.machine / wire.graph.
FACTORY_ACL_KEY_PATTERNS = (
    WORKORDER_STREAM,
    f"{FINISHED_PREFIX}*",
    f"{AGENTHANDLER_PREFIX}*",
    f"{GRAPHRUN_PREFIX}*",
    GRAPHEVENT_STREAM,
)

FACTORY_ACL_DENIED_COMMANDS = (
    "FLUSHDB",
    "FLUSHALL",
    "CONFIG",
    "ACL",
    "DEBUG",
    "MODULE",
    "SCRIPT",
)

_cached_password: Optional[str] = None


class FactoryAclError(RuntimeError):
    """Host Redis refused ACL setup; the sandbox must not get an admin URL."""


def factory_acl_password() -> str:
    """Stable password for this process. Env wins; otherwise generated once."""
    global _cached_password
    configured = (os.environ.get(ENV_FACTORY_ACL_PASSWORD) or "").strip()
    if configured:
        return configured
    if not _cached_password:
        _cached_password = secrets.token_urlsafe(24)
    return _cached_password


def factory_acl_setuser_args(password: str) -> list[str]:
    """Arguments after ``ACL SETUSER <user>`` (no username)."""
    rules = [
        "reset",
        "on",
        f">{password}",
        "resetkeys",
    ]
    rules.extend(f"~{pattern}" for pattern in FACTORY_ACL_KEY_PATTERNS)
    rules.extend(
        [
            "resetchannels",
            "-@all",
            "+@read",
            "+@write",
            "+@stream",
            "+@hash",
            "+@connection",
            "-@admin",
            "-@dangerous",
        ]
    )
    rules.extend(f"-{command}" for command in FACTORY_ACL_DENIED_COMMANDS)
    return rules


def factory_ipc_url(base_url: str, password: Optional[str] = None) -> str:
    """``base_url`` with the factory ACL userinfo. Do not log the result."""
    return redis_url_with_auth(
        base_url, FACTORY_ACL_USER, password or factory_acl_password()
    )


def ensure_factory_acl_user(redis_url: Optional[str] = None) -> str:
    """Create/update the factory ACL user as admin. Returns the password.

    Raises ``FactoryAclError`` when the operator's Redis cannot take ACL
    (no ACL command, or this client is not an admin).
    """
    url = resolve_redis_url(redis_url)
    password = factory_acl_password()
    try:
        admin = redis_connect(url, db=resolve_ephemeral_db(url))
    except Exception as exc:  # noqa: BLE001
        raise FactoryAclError(
            "Cannot reach Redis to apply the MachineFactory sandbox ACL. "
            "Start Redis or set REDIS_URL."
        ) from exc
    try:
        admin.execute_command(
            "ACL", "SETUSER", FACTORY_ACL_USER, *factory_acl_setuser_args(password)
        )
        admin.ping()
    except Exception as exc:  # noqa: BLE001
        raise FactoryAclError(
            "Redis refused ACL SETUSER for the MachineFactory sandbox user "
            f"{FACTORY_ACL_USER!r}. Apply the factory ACL as an admin user, "
            "or point REDIS_URL at a Redis 6+ instance this process can "
            "administer. Refusing to inject an unauthenticated factory URL."
        ) from exc
    finally:
        try:
            admin.close()
        except Exception:  # noqa: BLE001
            pass
    return password
