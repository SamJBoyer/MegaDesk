"""MachineFactory wire format: agent work done in a Docker sandbox on this machine.

(PUBSUB, db0) WORKORDER
  - execution signal; same fields as the stream. A PUBLISH starts work.
    An unsubscribed publish is dropped, so leftover tickets cannot re-run.

(STREAM, db0) WORKORDER
  - reference store written by the factory after it receives the signal
  - repo, URL, ref, ticket_name, instructions, model, auto_pr, pictures, issue

(HASH, db0) AGENTHANDLER:<guid>
  - ticket_id, status, error

(STREAM, db0) FINISHED:<REPO>
  - ticket_name, ticket_id, status, pr_url

MachineFactory clones the named repo into a Docker sandbox, gives the agent a
Redis sidecar as its ``REDIS_URL`` (so MegaDesk inside the sandbox never shares
the host live pair), and keeps factory IPC on ``MEGADESK_FACTORY_REDIS_URL`` —
the factory process's ephemeral DB on the host. When the agent finishes it hands
back a pull-request URL, not a worktree. PRManager does not read this stream:
it scans open PRs whose merge-check ``mergeable`` check succeeded.

The cloud family's counterpart is ``wire.cloud``, and the two are deliberately
the same shape: an order stream, a hash per live run, a finished stream. They
differ where the infrastructure differs. A machine order names a ``repo`` plus
clone ``URL`` and an optional ``auto_pr``; a cloud order names a ``repo_url`` /
``auto_pr``. Both carry an optional ``ref`` — the branch to start from, empty
meaning ``DEFAULT_STARTING_REF`` — because a gate that sends an agent at a
broken pull request has to put it on that PR's branch. Both hand back a PR URL
as the addressable result.

``FINISHED`` is per-repo rather than one stream so a factory FE can watch one
repo's outcomes without scanning every other.

The AGENTHANDLER hash is the run registry, and it is also the handshake: the
manager writes it before the sandbox starts, the sandbox reads its own ``guid``
out of the environment to find it, and whoever finishes deletes it. A missing
hash therefore means "no run", which is what makes the FE's live list truthful
without anyone having to reconcile it.
"""

from __future__ import annotations

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
    TERMINAL_STATUSES,
    is_terminal,
    normalize_status,
)

WORKORDER_CHANNEL = "WORKORDER"
WORKORDER_STREAM = "WORKORDER"
FINISHED_PREFIX = "FINISHED:"
AGENTHANDLER_PREFIX = "AGENTHANDLER:"

WORKORDER_GROUP = "machine_factory"
FINISHED_GROUP = "merge_manager"

__all__ = [
    "AGENTHANDLER_PREFIX",
    "DEFAULT_MODEL",
    "DEFAULT_STARTING_REF",
    "FINISHED_GROUP",
    "FINISHED_PREFIX",
    "RUN_STATUSES",
    "STATUS_CANCELLED",
    "STATUS_ERROR",
    "STATUS_FINISHED",
    "STATUS_QUEUED",
    "STATUS_RUNNING",
    "TERMINAL_STATUSES",
    "WORKORDER_CHANNEL",
    "WORKORDER_GROUP",
    "WORKORDER_STREAM",
    "agent_handler_fields",
    "agent_handler_key",
    "finished_fields",
    "finished_stream",
    "guid_from_agent_handler_key",
    "is_terminal",
    "load_workorder",
    "normalize_status",
    "parse_agent_handler",
    "parse_finished",
    "parse_workorder",
    "publish_workorder",
    "repo_from_finished_key",
    "workorder_fields",
]


def finished_stream(repo: str) -> str:
    text = stripped(repo)
    if not text:
        raise ValueError("FINISHED requires a repo")
    return f"{FINISHED_PREFIX}{text}"


def repo_from_finished_key(key: str) -> str:
    if not key.startswith(FINISHED_PREFIX):
        raise ValueError(f"Key {key!r} is not a {FINISHED_PREFIX}* stream")
    repo = key[len(FINISHED_PREFIX) :]
    if not repo:
        raise ValueError(f"Empty REPO in key {key!r}")
    return repo


def agent_handler_key(guid: str) -> str:
    text = stripped(guid)
    if not text:
        raise ValueError("AGENTHANDLER requires a guid")
    return f"{AGENTHANDLER_PREFIX}{text}"


def guid_from_agent_handler_key(key: str) -> str:
    if not key.startswith(AGENTHANDLER_PREFIX):
        raise ValueError(f"Key {key!r} is not a {AGENTHANDLER_PREFIX}* hash")
    guid = key[len(AGENTHANDLER_PREFIX) :]
    if not guid:
        raise ValueError(f"Empty guid in key {key!r}")
    return guid


# --- WORKORDER -------------------------------------------------------------


def publish_workorder(redis: Any, fields: Mapping[str, Any]) -> int:
    """PUBLISH a WORKORDER signal. The factory XADDs the stream itself."""
    return publish_fields(redis, WORKORDER_CHANNEL, fields)


def workorder_fields(
    *,
    repo: str,
    url: str,
    ticket_name: str,
    instructions: str,
    model: str = DEFAULT_MODEL,
    auto_pr: bool = True,
    ref: str = "",
    pictures: Any = "",
    issue: str = "",
) -> dict[str, str]:
    """Build a WORKORDER stream entry.

    ``URL`` is always required: the factory clones into the sandbox rather than
    mounting a Floor worktree. ``ref`` is optional and empty means
    ``DEFAULT_STARTING_REF``. ``pictures`` is a JSON list of image URLs the
    agent should see as context; empty means none. ``issue`` is the GitHub
    issue number when the order came from a labeled ticket; empty otherwise.
    """
    fields = {
        "repo": stripped(repo),
        "URL": stripped(url),
        "ref": stripped(ref),
        "ticket_name": stripped(ticket_name),
        "instructions": text_field(instructions),
        "model": stripped(model) or DEFAULT_MODEL,
        "auto_pr": bool_field(auto_pr),
        "pictures": pictures_field(pictures),
        "issue": stripped(issue),
    }
    require(
        "WORKORDER",
        fields,
        ("repo", "URL", "ticket_name", "instructions"),
    )
    return fields


def parse_workorder(fields: Mapping[str, Any]) -> dict[str, Any]:
    """Parse WORKORDER stream fields into a normalized dict."""
    parsed = {
        "repo": stripped(fields.get("repo")),
        "URL": stripped(fields.get("URL")),
        "ref": stripped(fields.get("ref")),
        "ticket_name": stripped(fields.get("ticket_name")),
        "instructions": text_field(fields.get("instructions")),
        "model": stripped(fields.get("model")) or DEFAULT_MODEL,
        "auto_pr": is_true(fields.get("auto_pr", True)),
        "pictures": parse_pictures(fields.get("pictures")),
        "issue": stripped(fields.get("issue")),
    }
    require(
        "WORKORDER",
        parsed,
        ("repo", "URL", "ticket_name", "instructions"),
    )
    return parsed


def load_workorder(redis: Any, ticket_id: str) -> dict[str, Any]:
    """Load and parse a WORKORDER stream entry by stream id.

    The order stays on the stream and the sandbox fetches it by id, so the
    instructions are never copied into the handshake hash and can never drift
    from what was actually ordered.
    """
    rows = redis.xrange(WORKORDER_STREAM, min=ticket_id, max=ticket_id, count=1)
    if not rows:
        raise LookupError(f"WORKORDER entry {ticket_id!r} not found")
    _entry_id, fields = rows[0]
    return parse_workorder(fields)


# --- FINISHED:<REPO> -------------------------------------------------------


def finished_fields(
    *,
    ticket_name: str,
    ticket_id: str,
    status: str,
    pr_url: str = "",
) -> dict[str, str]:
    """Build a FINISHED:<REPO> stream entry.

    ``pr_url`` may be empty on error paths; ``status`` is always one of
    ``RUN_STATUSES``.
    """
    fields = {
        "ticket_name": stripped(ticket_name),
        "ticket_id": stripped(ticket_id),
        "status": one_of(
            "FINISHED", "status", stripped(status), RUN_STATUSES
        ),
        "pr_url": stripped(pr_url),
    }
    require("FINISHED", fields, ("ticket_name", "ticket_id", "status"))
    return fields


def parse_finished(fields: Mapping[str, Any]) -> dict[str, str]:
    parsed = {
        "ticket_name": stripped(fields.get("ticket_name")),
        "ticket_id": stripped(fields.get("ticket_id")),
        "status": stripped(fields.get("status")),
        "pr_url": stripped(fields.get("pr_url")),
    }
    require("FINISHED", parsed, ("ticket_name", "ticket_id", "status"))
    one_of("FINISHED", "status", parsed["status"], RUN_STATUSES)
    return parsed


# --- AGENTHANDLER:<guid> ---------------------------------------------------


def agent_handler_fields(
    *,
    ticket_id: str,
    status: str = "",
    error: str = "",
) -> dict[str, str]:
    """``status`` may be empty only before the factory has decided on one."""
    fields = {
        "ticket_id": stripped(ticket_id),
        "status": stripped(status),
        "error": text_field(error),
    }
    require("AGENTHANDLER", fields, ("ticket_id",))
    if fields["status"]:
        one_of("AGENTHANDLER", "status", fields["status"], RUN_STATUSES)
    return fields


def parse_agent_handler(fields: Mapping[str, Any]) -> dict[str, str]:
    parsed = {
        "ticket_id": stripped(fields.get("ticket_id")),
        "status": stripped(fields.get("status")),
        "error": text_field(fields.get("error")),
    }
    require("AGENTHANDLER", parsed, ("ticket_id",))
    return parsed
