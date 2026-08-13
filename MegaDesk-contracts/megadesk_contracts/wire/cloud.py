"""CloudDispatcher wire format: documentation work sent to Cursor-hosted agents.

(STREAM, db0) CLOUDORDER
  - order_id, repo_url, ref, title, instructions, model, auto_pr

(STREAM, db0) CLOUDFINISHED
  - agent_id, order_id, status, pr_url

(HASH, db1) CLOUDRUN:<agent_id>
  - order_id, repo_url, title, status, pr_url, run_id

(HASH, db1) CLOUDDRAFT:<order_id>
  - the CLOUDORDER field set, held back rather than published

A draft is an order nobody has agreed to yet. VoiceDeck writes one instead of
publishing CLOUDORDER, because a spoken sentence should not be able to open a
pull request on its own; pressing dispatch in the FE turns the hash into the
stream entry unchanged, which is why it carries exactly the order's fields.

A cloud agent clones the repo onto Cursor's own VM and pushes a branch, so
``repo_url`` is the whole input — there is no local worktree to hand over and
nothing for MergeManager to merge. ``order_id`` is minted by the dispatcher and
survives the round trip so a FE row can be reconciled with its run even after a
restart, when the only other identifier is Cursor's ``bc-`` agent id.
"""

from __future__ import annotations

import uuid
from typing import Any, Mapping

from megadesk_contracts.wire._fields import (
    bool_field,
    is_true,
    one_of,
    require,
    stripped,
    text_field,
)

CLOUDORDER_STREAM = "CLOUDORDER"
CLOUDFINISHED_STREAM = "CLOUDFINISHED"
CLOUDRUN_PREFIX = "CLOUDRUN:"
CLOUDDRAFT_PREFIX = "CLOUDDRAFT:"
CLOUDORDER_GROUP = "cloud_dispatcher"

DEFAULT_MODEL = "auto"
CLOUD_AGENT_ID_PREFIX = "bc-"

STATUS_DRAFT = "draft"
STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_FINISHED = "finished"
STATUS_ERROR = "error"
STATUS_CANCELLED = "cancelled"
# The run never started: auth, config or network. Distinct from ``error``, which
# means the agent ran and failed, because only one of the two is worth retrying.
# See ``megadesk_contracts.agent_errors`` for the exceptions a runtime raises.
STATUS_STARTUP_ERROR = "startup_error"

RUN_STATUSES = frozenset(
    {
        STATUS_DRAFT,
        STATUS_QUEUED,
        STATUS_RUNNING,
        STATUS_FINISHED,
        STATUS_ERROR,
        STATUS_CANCELLED,
        STATUS_STARTUP_ERROR,
    }
)
TERMINAL_STATUSES = frozenset(
    {STATUS_FINISHED, STATUS_ERROR, STATUS_CANCELLED, STATUS_STARTUP_ERROR}
)


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


def clouddraft_key(order_id: str) -> str:
    text = stripped(order_id)
    if not text:
        raise ValueError("CLOUDDRAFT requires an order_id")
    return f"{CLOUDDRAFT_PREFIX}{text}"


def order_id_from_draft_key(key: str) -> str:
    if not key.startswith(CLOUDDRAFT_PREFIX):
        raise ValueError(f"Key {key!r} is not a {CLOUDDRAFT_PREFIX}* hash")
    order_id = key[len(CLOUDDRAFT_PREFIX) :]
    if not order_id:
        raise ValueError(f"Empty order_id in key {key!r}")
    return order_id


def new_order_id() -> str:
    return uuid.uuid4().hex


def is_cloud_agent_id(agent_id: str) -> bool:
    """Cursor routes ``bc-`` prefixed ids to the cloud API; anything else is local."""
    return stripped(agent_id).startswith(CLOUD_AGENT_ID_PREFIX)


# --- CLOUDORDER ------------------------------------------------------------


def cloudorder_fields(
    *,
    order_id: str,
    repo_url: str,
    title: str,
    instructions: str,
    model: str = DEFAULT_MODEL,
    auto_pr: bool = True,
    ref: str = "",
) -> dict[str, str]:
    fields = {
        "order_id": stripped(order_id),
        "repo_url": stripped(repo_url),
        "ref": stripped(ref),
        "title": stripped(title),
        "instructions": text_field(instructions),
        "model": stripped(model) or DEFAULT_MODEL,
        "auto_pr": bool_field(auto_pr),
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
    """``agent_id`` is empty only for ``startup_error``: no run, so no id."""
    fields = {
        "agent_id": stripped(agent_id),
        "order_id": stripped(order_id),
        "status": one_of(
            "CLOUDFINISHED", "status", stripped(status), TERMINAL_STATUSES
        ),
        "pr_url": stripped(pr_url),
    }
    require("CLOUDFINISHED", fields, ("order_id",))
    if not fields["agent_id"] and fields["status"] != STATUS_STARTUP_ERROR:
        raise ValueError(
            f"CLOUDFINISHED requires agent_id unless status is {STATUS_STARTUP_ERROR}"
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
