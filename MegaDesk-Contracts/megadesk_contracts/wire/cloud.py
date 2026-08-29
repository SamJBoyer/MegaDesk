"""CloudFactory wire format: work sent to Cursor-hosted agents.

(PUBSUB, db0) CLOUDORDER
  - execution signal; same fields as the stream. A PUBLISH starts work.
    An unsubscribed publish is dropped, so leftover tickets cannot re-run.

(STREAM, db0) CLOUDORDER
  - reference store written by the factory after it receives the signal
  - order_id, repo_url, ref, title, instructions, model, auto_pr, pictures

(STREAM, db0) CLOUDFINISHED
  - agent_id, order_id, status, pr_url
    agent_id is empty when no agent exists: startup_error, or cancelled before launch

(HASH, db1) CLOUDRUN:<agent_id>
  - order_id, repo_url, title, status, pr_url, run_id

The machine family's counterpart is ``wire.machine``, and the shape is the same:
an order stream, a hash per live run, a finished stream. The statuses are shared
outright — see ``wire.factory`` — so a graph can watch a run without knowing
which kind of factory started it.

Where this family differs is infrastructure, not taste. A cloud agent clones the
repo onto Cursor's own VM and pushes a branch, so ``repo_url`` is the whole input
and a pull request is the whole output: there is no local worktree to hand over.
The run also lives on db 1 rather than
db 0, because it outlives this process by minutes and a machine sandbox does not.
``order_id`` is minted before launch and survives the round trip, so a FE row can
be reconciled with its run after a restart, when the only other identifier is
Cursor's ``bc-`` agent id.
"""

from __future__ import annotations

import uuid
from typing import Any, Mapping

from megadesk_contracts.wire._fields import (
    bool_field,
    is_true,
    one_of,
    parse_pictures,
    pictures_field,
    require,
    stripped,
    text_field,
)
from megadesk_contracts.wire.signal import publish_fields
from megadesk_contracts.wire.factory import (
    DEFAULT_MODEL,
    DEFAULT_STARTING_REF,
    RUN_STATUSES,
    STATUS_CANCELLED,
    STATUS_ERROR,
    STATUS_FINISHED,
    STATUS_QUEUED,
    STATUS_RUNNING,
    STATUS_STARTUP_ERROR,
    TERMINAL_STATUSES,
    is_terminal,
    normalize_status,
)

CLOUDORDER_CHANNEL = "CLOUDORDER"
CLOUDORDER_STREAM = "CLOUDORDER"
CLOUDFINISHED_STREAM = "CLOUDFINISHED"
CLOUDRUN_PREFIX = "CLOUDRUN:"
CLOUDORDER_GROUP = "cloud_factory"

CLOUD_AGENT_ID_PREFIX = "bc-"

# The shared statuses are re-exported so a caller working in one family can spell
# every status it needs off one module.
__all__ = [
    "CLOUDFINISHED_STREAM",
    "CLOUDORDER_CHANNEL",
    "CLOUDORDER_GROUP",
    "CLOUDORDER_STREAM",
    "CLOUDRUN_PREFIX",
    "CLOUD_AGENT_ID_PREFIX",
    "DEFAULT_MODEL",
    "DEFAULT_STARTING_REF",
    "RUN_STATUSES",
    "STATUS_CANCELLED",
    "STATUS_ERROR",
    "STATUS_FINISHED",
    "STATUS_QUEUED",
    "STATUS_RUNNING",
    "STATUS_STARTUP_ERROR",
    "TERMINAL_STATUSES",
    "agent_id_from_key",
    "cloudfinished_fields",
    "cloudorder_fields",
    "cloudrun_fields",
    "cloudrun_key",
    "is_cloud_agent_id",
    "is_terminal",
    "new_order_id",
    "normalize_status",
    "parse_cloudfinished",
    "parse_cloudorder",
    "parse_cloudrun",
    "publish_cloudorder",
]


def cloudrun_key(agent_id: str) -> str:
    text = stripped(agent_id)
    if not text:
        raise ValueError("CLOUDRUN requires an agent_id")
    return f"{CLOUDRUN_PREFIX}{text}"


def agent_id_from_key(key: str) -> str:
    if not key.startswith(CLOUDRUN_PREFIX):
        raise ValueError(f"Key {key!r} is not a {CLOUDRUN_PREFIX}* hash")
    agent_id = key[len(CLOUDRUN_PREFIX) :]
    if not agent_id:
        raise ValueError(f"Empty agent_id in key {key!r}")
    return agent_id


def new_order_id() -> str:
    return uuid.uuid4().hex


def is_cloud_agent_id(agent_id: str) -> bool:
    """Cursor routes ``bc-`` prefixed ids to the cloud API; anything else is local."""
    return stripped(agent_id).startswith(CLOUD_AGENT_ID_PREFIX)


# --- CLOUDORDER ------------------------------------------------------------


def publish_cloudorder(redis: Any, fields: Mapping[str, Any]) -> int:
    """PUBLISH a CLOUDORDER signal. The factory XADDs the stream itself."""
    return publish_fields(redis, CLOUDORDER_CHANNEL, fields)


def cloudorder_fields(
    *,
    order_id: str,
    repo_url: str,
    title: str,
    instructions: str,
    model: str = DEFAULT_MODEL,
    auto_pr: bool = True,
    ref: str = "",
    pictures: Any = "",
) -> dict[str, str]:
    fields = {
        "order_id": stripped(order_id),
        "repo_url": stripped(repo_url),
        "ref": stripped(ref),
        "title": stripped(title),
        "instructions": text_field(instructions),
        "model": stripped(model) or DEFAULT_MODEL,
        "auto_pr": bool_field(auto_pr),
        "pictures": pictures_field(pictures),
    }
    require(
        "CLOUDORDER", fields, ("order_id", "repo_url", "title", "instructions")
    )
    return fields


def parse_cloudorder(fields: Mapping[str, Any]) -> dict[str, Any]:
    parsed = {
        "order_id": stripped(fields.get("order_id")),
        "repo_url": stripped(fields.get("repo_url")),
        "ref": stripped(fields.get("ref")),
        "title": stripped(fields.get("title")),
        "instructions": text_field(fields.get("instructions")),
        "model": stripped(fields.get("model")) or DEFAULT_MODEL,
        "auto_pr": is_true(fields.get("auto_pr", True)),
        "pictures": parse_pictures(fields.get("pictures")),
    }
    require(
        "CLOUDORDER", parsed, ("order_id", "repo_url", "title", "instructions")
    )
    return parsed


# --- CLOUDFINISHED ---------------------------------------------------------


def cloudfinished_fields(
    *,
    order_id: str,
    status: str,
    agent_id: str = "",
    pr_url: str = "",
) -> dict[str, str]:
    """``agent_id`` is empty when no agent exists.

    That is ``startup_error`` (the run never started) and ``cancelled`` (the
    order was rejected before Cursor minted an id). A run that executed always
    carries its ``bc-`` id.
    """
    fields = {
        "agent_id": stripped(agent_id),
        "order_id": stripped(order_id),
        "status": one_of(
            "CLOUDFINISHED", "status", stripped(status), TERMINAL_STATUSES
        ),
        "pr_url": stripped(pr_url),
    }
    require("CLOUDFINISHED", fields, ("order_id",))
    if not fields["agent_id"] and fields["status"] not in {
        STATUS_STARTUP_ERROR,
        STATUS_CANCELLED,
    }:
        raise ValueError(
            "CLOUDFINISHED requires agent_id unless status is "
            f"{STATUS_STARTUP_ERROR} or {STATUS_CANCELLED}"
        )
    return fields


def parse_cloudfinished(fields: Mapping[str, Any]) -> dict[str, str]:
    parsed = {
        "agent_id": stripped(fields.get("agent_id")),
        "order_id": stripped(fields.get("order_id")),
        "status": stripped(fields.get("status")),
        "pr_url": stripped(fields.get("pr_url")),
    }
    require("CLOUDFINISHED", parsed, ("order_id", "status"))
    one_of("CLOUDFINISHED", "status", parsed["status"], TERMINAL_STATUSES)
    return parsed


# --- CLOUDRUN:<agent_id> --------------------------------------------------


def cloudrun_fields(
    *,
    order_id: str,
    repo_url: str,
    title: str,
    status: str,
    pr_url: str = "",
    run_id: str = "",
) -> dict[str, str]:
    fields = {
        "order_id": stripped(order_id),
        "repo_url": stripped(repo_url),
        "title": stripped(title),
        "status": one_of("CLOUDRUN", "status", stripped(status), RUN_STATUSES),
        "pr_url": stripped(pr_url),
        "run_id": stripped(run_id),
    }
    require("CLOUDRUN", fields, ("order_id", "repo_url", "title"))
    return fields


def parse_cloudrun(fields: Mapping[str, Any]) -> dict[str, str]:
    parsed = {
        "order_id": stripped(fields.get("order_id")),
        "repo_url": stripped(fields.get("repo_url")),
        "title": stripped(fields.get("title")),
        "status": stripped(fields.get("status")),
        "pr_url": stripped(fields.get("pr_url")),
        "run_id": stripped(fields.get("run_id")),
    }
    require("CLOUDRUN", parsed, ("order_id", "repo_url", "title", "status"))
    one_of("CLOUDRUN", "status", parsed["status"], RUN_STATUSES)
    return parsed
