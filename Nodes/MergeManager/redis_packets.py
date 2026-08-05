"""Standard Redis packet shapes (Plant/prompt2).

Streams / hashes used across Plant, TicketDispatcher, and MergeManager:

(STREAM) WORKORDER
  - repo
  - URL
  - new_wt        "true" | "false"
  - wt            absolute path when new_wt is false
  - ticket_name
  - instructions
  - model         default "auto"

(HASH) LIVEHARNESS:<GUID>
  - ticket_id     WORKORDER stream id
  - status
  - error

(STREAM) FINISHED:<REPO>
  - ticket_name
  - ticket_id
  - wt            absolute path to ticket worktree
  - agent_dir     absolute path to agents worktree
"""

from __future__ import annotations

from typing import Any, Mapping

WORKORDER_STREAM = "WORKORDER"
FINISHED_PREFIX = "FINISHED:"
LIVEHARNESS_PREFIX = "LIVEHARNESS:"
DEFAULT_MODEL = "auto"

BOOL_TRUE = "true"
BOOL_FALSE = "false"


def finished_stream(repo: str) -> str:
    return f"{FINISHED_PREFIX}{repo}"


def harness_key(guid: str) -> str:
    return f"{LIVEHARNESS_PREFIX}{guid}"


def repo_from_finished_key(key: str) -> str:
    if not key.startswith(FINISHED_PREFIX):
        raise ValueError(f"Key {key!r} is not a FINISHED:* stream")
    repo = key[len(FINISHED_PREFIX) :]
    if not repo:
        raise ValueError(f"Empty REPO in key {key!r}")
    return repo


def bool_field(value: Any) -> str:
    if isinstance(value, bool):
        return BOOL_TRUE if value else BOOL_FALSE
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "y"):
        return BOOL_TRUE
    if text in ("0", "false", "no", "n", ""):
        return BOOL_FALSE
    raise ValueError(f"Invalid boolean field: {value!r}")


def is_true(value: Any) -> bool:
    return bool_field(value) == BOOL_TRUE


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
    """Build a WORKORDER stream entry (all values are strings for Redis)."""
    fields = {
        "repo": str(repo).strip(),
        "URL": str(url).strip(),
        "new_wt": bool_field(new_wt),
        "wt": str(wt).strip() if not new_wt else "",
        "ticket_name": str(ticket_name).strip(),
        "instructions": str(instructions),
        "model": (str(model).strip() or DEFAULT_MODEL),
    }
    if not fields["repo"]:
        raise ValueError("WORKORDER requires repo")
    if not fields["ticket_name"]:
        raise ValueError("WORKORDER requires ticket_name")
    if not fields["instructions"]:
        raise ValueError("WORKORDER requires instructions")
    if not new_wt and not fields["wt"]:
        raise ValueError("WORKORDER with new_wt=false requires wt")
    return fields


def parse_workorder(fields: Mapping[str, Any]) -> dict[str, Any]:
    """Parse WORKORDER stream fields into a normalized dict."""
    repo = fields.get("repo") or fields.get("REPO")
    url = fields.get("URL") or fields.get("url") or ""
    ticket_name = (
        fields.get("ticket_name")
        or fields.get("ticket")
        or fields.get("name")
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
    new_wt = is_true(fields.get("new_wt", BOOL_TRUE))
    wt = str(fields.get("wt") or "").strip()
    if not new_wt and not wt:
        raise ValueError("WORKORDER with new_wt=false requires wt")
    return {
        "repo": str(repo).strip(),
        "URL": str(url).strip(),
        "new_wt": new_wt,
        "wt": wt,
        "ticket_name": str(ticket_name).strip(),
        "instructions": str(instructions),
        "model": str(fields.get("model") or DEFAULT_MODEL).strip() or DEFAULT_MODEL,
    }


def finished_fields(
    *,
    ticket_name: str,
    ticket_id: str,
    wt: str,
    agent_dir: str,
) -> dict[str, str]:
    """Build a FINISHED:<REPO> stream entry."""
    fields = {
        "ticket_name": str(ticket_name).strip(),
        "ticket_id": str(ticket_id).strip(),
        "wt": str(wt).strip(),
        "agent_dir": str(agent_dir).strip(),
    }
    missing = [k for k, v in fields.items() if not v]
    if missing:
        raise ValueError(f"FINISHED entry missing: {', '.join(missing)}")
    return fields


def parse_finished(fields: Mapping[str, Any]) -> dict[str, str]:
    """Parse FINISHED:<REPO> stream fields into a normalized dict."""
    ticket_name = fields.get("ticket_name") or fields.get("ticket") or ""
    ticket_id = fields.get("ticket_id") or ""
    wt = fields.get("wt") or fields.get("workpath") or ""
    agent_dir = fields.get("agent_dir") or ""
    parsed = {
        "ticket_name": str(ticket_name).strip(),
        "ticket_id": str(ticket_id).strip(),
        "wt": str(wt).strip(),
        "agent_dir": str(agent_dir).strip(),
    }
    missing = [k for k, v in parsed.items() if not v]
    if missing:
        raise ValueError(f"FINISHED entry missing: {', '.join(missing)}")
    return parsed


def harness_fields(
    *,
    ticket_id: str,
    status: str = "",
    error: str = "",
) -> dict[str, str]:
    """Build a LIVEHARNESS:<GUID> hash."""
    if not str(ticket_id).strip():
        raise ValueError("LIVEHARNESS requires ticket_id")
    return {
        "ticket_id": str(ticket_id).strip(),
        "status": str(status),
        "error": str(error),
    }


def parse_harness(fields: Mapping[str, Any]) -> dict[str, str]:
    ticket_id = fields.get("ticket_id") or ""
    if not str(ticket_id).strip():
        raise ValueError("LIVEHARNESS hash missing ticket_id")
    return {
        "ticket_id": str(ticket_id).strip(),
        "status": str(fields.get("status") or ""),
        "error": str(fields.get("error") or ""),
    }


def load_workorder(redis: Any, ticket_id: str) -> dict[str, Any]:
    """Load and parse a WORKORDER stream entry by stream id."""
    rows = redis.xrange(WORKORDER_STREAM, min=ticket_id, max=ticket_id, count=1)
    if not rows:
        raise LookupError(f"WORKORDER entry {ticket_id!r} not found")
    _entry_id, fields = rows[0]
    return parse_workorder(fields)


def merge_workorder_instructions(
    *,
    repo: str,
    wt: str,
    agent_dir: str,
    ticket_name: str,
) -> str:
    """Instructions published when a local merge hits conflicts."""
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
