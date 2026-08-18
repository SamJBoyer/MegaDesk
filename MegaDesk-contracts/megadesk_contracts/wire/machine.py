"""MachineFactory wire format: agent work done in a worktree on this machine.

(STREAM, db0) WORKORDER
  - repo, URL, new_wt, wt, ticket_name, instructions, model

(HASH, db0) AGENTHANDLER:<guid>
  - ticket_id, status, error

(STREAM, db0) FINISHED:<REPO>
  - ticket_name, ticket_id, wt, agent_dir

The cloud family's counterpart is ``wire.cloud``, and the two are deliberately
the same shape: an order stream, a hash per live run, a finished stream. They
differ where the infrastructure differs. A machine order names a ``repo`` on the
Floor and hands back a ``wt`` — an absolute path to a worktree that still exists
after the agent stops, which is the whole point, because MergeManager has to go
and merge it. A cloud order names a ``repo_url`` and hands back a pull request;
there is no local tree for anyone to merge.

``FINISHED`` is per-repo rather than one stream because MergeManager watches the
repos it has checked out, not every repo the Floor knows about.

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
    require,
    stripped,
    text_field,
)
from megadesk_contracts.wire.factory import (
    DEFAULT_MODEL,
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

WORKORDER_STREAM = "WORKORDER"
FINISHED_PREFIX = "FINISHED:"
AGENTHANDLER_PREFIX = "AGENTHANDLER:"

WORKORDER_GROUP = "machine_factory"
FINISHED_GROUP = "merge_manager"

__all__ = [
    "AGENTHANDLER_PREFIX",
    "DEFAULT_MODEL",
    "FINISHED_GROUP",
    "FINISHED_PREFIX",
    "RUN_STATUSES",
    "STATUS_CANCELLED",
    "STATUS_ERROR",
    "STATUS_FINISHED",
    "STATUS_QUEUED",
    "STATUS_RUNNING",
    "TERMINAL_STATUSES",
    "WORKORDER_GROUP",
    "WORKORDER_STREAM",
    "agent_handler_fields",
    "agent_handler_key",
    "finished_fields",
    "finished_stream",
    "guid_from_agent_handler_key",
    "is_terminal",
    "load_workorder",
    "merge_workorder_instructions",
    "normalize_status",
    "parse_agent_handler",
    "parse_finished",
    "parse_workorder",
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


def workorder_fields(
    *,
    repo: str,
    url: str,
    new_wt: bool,
    ticket_name: str,
    instructions: str,
    model: str = DEFAULT_MODEL,
    wt: str = "",
) -> dict[str, str]:
    """Build a WORKORDER stream entry.

    ``wt`` is cleared when ``new_wt`` is true: the factory is about to create the
    worktree, so any path the caller guessed would be a lie by the time it lands.
    """
    fields = {
        "repo": stripped(repo),
        "URL": stripped(url),
        "new_wt": bool_field(new_wt),
        "wt": stripped(wt) if not new_wt else "",
        "ticket_name": stripped(ticket_name),
        "instructions": text_field(instructions),
        "model": stripped(model) or DEFAULT_MODEL,
    }
    require("WORKORDER", fields, ("repo", "ticket_name", "instructions"))
    if not new_wt and not fields["wt"]:
        raise ValueError("WORKORDER with new_wt=false requires wt")
    return fields


def parse_workorder(fields: Mapping[str, Any]) -> dict[str, Any]:
    """Parse WORKORDER stream fields into a normalized dict.

    The aliases are read-side only. Anything published through
    ``workorder_fields`` uses the canonical names; these let a hand-written or
    older entry still be consumed rather than dropped.
    """
    repo = fields.get("repo") or fields.get("REPO")
    url = fields.get("URL") or fields.get("url") or ""
    ticket_name = (
        fields.get("ticket_name") or fields.get("ticket") or fields.get("name")
    )
    instructions = (
        fields.get("instructions")
        or fields.get("instruction")
        or fields.get("prompt")
        or fields.get("text")
    )
    if not repo or not ticket_name or not instructions:
        raise ValueError(
            "WORKORDER requires repo, ticket_name, and instructions; "
            f"got keys={list(fields.keys())}"
        )
    new_wt = is_true(fields.get("new_wt", True))
    wt = stripped(fields.get("wt"))
    if not new_wt and not wt:
        raise ValueError("WORKORDER with new_wt=false requires wt")
    return {
        "repo": stripped(repo),
        "URL": stripped(url),
        "new_wt": new_wt,
        "wt": wt,
        "ticket_name": stripped(ticket_name),
        "instructions": text_field(instructions),
        "model": stripped(fields.get("model")) or DEFAULT_MODEL,
    }


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
    wt: str,
    agent_dir: str,
) -> dict[str, str]:
    fields = {
        "ticket_name": stripped(ticket_name),
        "ticket_id": stripped(ticket_id),
        "wt": stripped(wt),
        "agent_dir": stripped(agent_dir),
    }
    require("FINISHED", fields, tuple(fields))
    return fields


def parse_finished(fields: Mapping[str, Any]) -> dict[str, str]:
    parsed = {
        "ticket_name": stripped(fields.get("ticket_name") or fields.get("ticket")),
        "ticket_id": stripped(fields.get("ticket_id")),
        "wt": stripped(fields.get("wt") or fields.get("workpath")),
        "agent_dir": stripped(fields.get("agent_dir")),
    }
    require("FINISHED", parsed, tuple(parsed))
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


# --- MergeManager follow-up ------------------------------------------------


def merge_workorder_instructions(
    *,
    repo: str,
    wt: str,
    agent_dir: str,
    ticket_name: str,
) -> str:
    """Instructions published when a local merge hits conflicts.

    The follow-up order sets ``new_wt=false`` and points at the tree that
    already has the conflict in it, because resolving a conflict somewhere else
    would resolve nothing.
    """
    return (
        f"Resolve merge conflicts for ticket {ticket_name!r} in repo {repo}.\n\n"
        f"Context:\n"
        f"- Ticket worktree (absolute path): {wt}\n"
        f"- Agents worktree (absolute path): {agent_dir}\n"
        f"- new_wt is false; work in the existing worktree at the mounted path.\n"
        f"- Cleanly merge the ticket branch into agents.\n"
        f"- Resolve every conflict carefully and leave agents buildable.\n"
        f"- Commit the merge with a clear message naming the ticket and what was merged.\n"
    )
