"""Standard Redis packet shapes (MissionControl/prompt2).

(STREAM) WORKORDER
  - repo, URL, new_wt, wt, ticket_name, instructions, model

(HASH) AGENTHANDLER:<GUID>
  - ticket_id, status, error

(STREAM) FINISHED:<REPO>
  - ticket_name, ticket_id, wt, agent_dir
"""

from __future__ import annotations

from typing import Any, Mapping

WORKORDER_STREAM = "WORKORDER"
FINISHED_PREFIX = "FINISHED:"
AGENTHANDLER_PREFIX = "AGENTHANDLER:"
DEFAULT_MODEL = "auto"

BOOL_TRUE = "true"
BOOL_FALSE = "false"


def finished_stream(repo: str) -> str:
    return f"{FINISHED_PREFIX}{repo}"


def agent_handler_key(guid: str) -> str:
    return f"{AGENTHANDLER_PREFIX}{guid}"


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


def agent_handler_fields(
    *,
    ticket_id: str,
    status: str = "",
    error: str = "",
) -> dict[str, str]:
    if not str(ticket_id).strip():
        raise ValueError("AGENTHANDLER requires ticket_id")
    return {
        "ticket_id": str(ticket_id).strip(),
        "status": str(status),
        "error": str(error),
    }


def parse_agent_handler(fields: Mapping[str, Any]) -> dict[str, str]:
    ticket_id = fields.get("ticket_id") or ""
    if not str(ticket_id).strip():
        raise ValueError("AGENTHANDLER hash missing ticket_id")
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
