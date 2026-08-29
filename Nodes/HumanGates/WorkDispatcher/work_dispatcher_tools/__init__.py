"""Voice tools for WorkDispatcher: list tickets, choose one, set target, send."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from megadesk_contracts import ToolSpec
from megadesk_contracts.human_gate import (
    LABEL_AGENT_READY,
    check_repo,
    extract_issue_pictures,
    list_labeled_issues,
    normalize_repo_url,
    parse_github_repo,
    run_gh as default_run_gh,
)
from megadesk_contracts.wire.cloud import (
    CLOUDORDER_STREAM,
    cloudorder_fields,
    new_order_id,
)
from megadesk_contracts.wire.factory import DEFAULT_MODEL
from megadesk_contracts.wire.machine import WORKORDER_STREAM, workorder_fields
from megadesk_contracts.wire.voice import KIND_DISPATCH, KIND_ERROR

NODE_NAME = "work_dispatcher"
TOOL_LIST_TICKETS = "list_tickets"
TOOL_CHOOSE_TICKET = "choose_ticket"
TOOL_SET_DISPATCH = "set_dispatch"
TOOL_SEND_TICKET = "send_ticket"

FACTORY_OPTIONS = ("machine", "cloud")
MODEL_OPTIONS = ("auto", "grok-4.6", "claude-opus-5")
DEFAULT_FACTORY = "machine"
DEFAULT_LABEL = LABEL_AGENT_READY

# Patchable stand-in for the GitHub CLI, same pattern as work_dispatcher_app.
run_gh = default_run_gh

INSTRUCTIONS = f"""Work tickets are labeled GitHub issues. To send one, call \
{TOOL_LIST_TICKETS}, then {TOOL_CHOOSE_TICKET}, then {TOOL_SET_DISPATCH} if the \
user names machine or cloud or a model, then {TOOL_SEND_TICKET}. Say the ticket \
title and destination back and wait for agreement before sending. \
{TOOL_LIST_TICKETS} reports how many tickets are waiting and their titles."""


@dataclass
class _Ticket:
    id: int
    name: str
    body: str
    url: str


@dataclass
class _Draft:
    tickets: dict[int, _Ticket] = field(default_factory=dict)
    chosen_id: Optional[int] = None
    factory: str = DEFAULT_FACTORY
    model: str = DEFAULT_MODEL
    repo_url: str = ""
    repo_name: str = ""
    label: str = DEFAULT_LABEL


_draft = _Draft()


def reset_draft() -> None:
    """Forget the in-flight ticket choice. Tests call this between cases."""
    global _draft
    _draft = _Draft()


def parse_repo_ref(text: str) -> Optional[tuple[str, str, str]]:
    """Return ``(owner, repo, url)`` from a GitHub URL or ``owner/repo`` slug."""
    raw = (text or "").strip()
    if not raw:
        return None
    parsed = parse_github_repo(raw)
    if parsed:
        owner, repo = parsed
        return owner, repo, normalize_repo_url(raw, owner, repo)
    parts = [p for p in raw.strip("/").split("/") if p]
    if len(parts) != 2:
        return None
    owner, repo = parts
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not owner or not repo:
        return None
    return owner, repo, f"https://github.com/{owner}/{repo}"


def _resolve_repo(arguments: dict, host: Any) -> tuple[str, str, str] | dict:
    raw = str(arguments.get("repo") or arguments.get("url") or "").strip()
    if not raw:
        raw = _draft.repo_url
    if not raw:
        resolved = host.resolve_scope_session(getattr(host, "target_repo", "") or "")
        if resolved is not None:
            session_id, repo = resolved
            try:
                raw = host.repo_url(session_id)
            except Exception:
                raw = repo
    parsed = parse_repo_ref(raw)
    if parsed is None:
        return {
            "status": "error",
            "detail": (
                "no GitHub repository is set; pass owner/repo or a GitHub URL"
            ),
        }
    return parsed


def handle_list_tickets(arguments: dict, host: Any) -> dict:
    resolved = _resolve_repo(arguments, host)
    if isinstance(resolved, dict):
        return resolved
    owner, repo, url = resolved
    label = str(arguments.get("label") or "").strip() or DEFAULT_LABEL
    ok, err = check_repo(owner, repo, gh=run_gh)
    if not ok:
        detail = err or "could not reach GitHub"
        host.publish(KIND_ERROR, detail)
        return {"status": "error", "detail": detail}
    ok, issues, err = list_labeled_issues(owner, repo, label, gh=run_gh)
    if not ok:
        return {"status": "error", "detail": err or "could not list tickets"}

    _draft.tickets = {
        issue.number: _Ticket(
            id=issue.number, name=issue.title, body=issue.body, url=url
        )
        for issue in issues
    }
    _draft.repo_url = url
    _draft.repo_name = repo
    _draft.label = label
    titles = [{"id": ticket.id, "title": ticket.name} for ticket in _draft.tickets.values()]
    return {
        "status": "ok",
        "count": len(titles),
        "label": label,
        "repo": f"{owner}/{repo}",
        "tickets": titles,
        "detail": f"{len(titles)} {label} ticket(s) on {owner}/{repo}",
    }


def handle_choose_ticket(arguments: dict, host: Any) -> dict:
    raw = arguments.get("ticket") or arguments.get("id") or arguments.get("number")
    if raw is None or str(raw).strip() == "":
        return {"status": "error", "detail": "no ticket was named"}
    key = str(raw).strip()
    ticket = None
    if key.isdigit():
        ticket = _draft.tickets.get(int(key))
    if ticket is None:
        lowered = key.lower()
        matches = [
            item for item in _draft.tickets.values() if item.name.lower() == lowered
        ]
        if len(matches) == 1:
            ticket = matches[0]
    if ticket is None:
        return {
            "status": "error",
            "detail": (
                f"ticket {key} is not in the current list; "
                f"call {TOOL_LIST_TICKETS} first"
            ),
            "available": [
                {"id": item.id, "title": item.name} for item in _draft.tickets.values()
            ],
        }
    _draft.chosen_id = ticket.id
    return {"status": "ok", "id": ticket.id, "title": ticket.name}


def handle_set_dispatch(arguments: dict, host: Any) -> dict:
    factory = str(
        arguments.get("factory") or arguments.get("target") or ""
    ).strip().lower()
    model = str(arguments.get("model") or "").strip()
    if factory:
        if factory not in FACTORY_OPTIONS:
            return {
                "status": "error",
                "detail": f"factory must be machine or cloud, not {factory}",
            }
        _draft.factory = factory
    if model:
        _draft.model = model
    if not factory and not model:
        return {
            "status": "error",
            "detail": "name a factory (machine or cloud) and/or a model",
        }
    return {"status": "ok", "factory": _draft.factory, "model": _draft.model}


def handle_send_ticket(arguments: dict, host: Any) -> dict:
    if _draft.chosen_id is None:
        return {
            "status": "error",
            "detail": f"no ticket is chosen; call {TOOL_CHOOSE_TICKET} first",
        }
    ticket = _draft.tickets.get(_draft.chosen_id)
    if ticket is None:
        return {"status": "error", "detail": "the chosen ticket is no longer in the list"}

    factory = (
        str(arguments.get("factory") or "").strip().lower() or _draft.factory
    )
    model = str(arguments.get("model") or "").strip() or _draft.model
    if factory not in FACTORY_OPTIONS:
        return {"status": "error", "detail": f"unknown factory {factory}"}

    repo_url = ticket.url or _draft.repo_url
    parsed = parse_github_repo(repo_url)
    if parsed:
        owner, repo = parsed
        repo_url = normalize_repo_url(repo_url, owner, repo)
        repo_name = repo
    else:
        repo_name = _draft.repo_name or repo_url.rstrip("/").rsplit("/", 1)[-1]
        if repo_name.endswith(".git"):
            repo_name = repo_name[:-4]

    instructions = ticket.body or ticket.name
    pictures = extract_issue_pictures(ticket.body)
    try:
        if factory == "machine":
            stream = WORKORDER_STREAM
            fields = workorder_fields(
                repo=repo_name,
                url=repo_url,
                ticket_name=ticket.name,
                instructions=instructions,
                model=model,
                auto_pr=True,
                pictures=pictures,
            )
        else:
            stream = CLOUDORDER_STREAM
            fields = cloudorder_fields(
                order_id=new_order_id(),
                repo_url=repo_url,
                title=ticket.name,
                instructions=instructions,
                model=model,
                auto_pr=True,
                pictures=pictures,
            )
    except ValueError as exc:
        return {"status": "error", "detail": str(exc)}

    try:
        host.ephemeral.xadd(stream, fields)
    except Exception as exc:  # noqa: BLE001 - a failed send must still answer
        return {"status": "error", "detail": f"Redis xadd failed: {exc}"}

    host.publish(KIND_DISPATCH, f"queued: {ticket.name} → {stream}")
    return {
        "status": "queued",
        "id": ticket.id,
        "title": ticket.name,
        "factory": factory,
        "model": model,
        "stream": stream,
        "detail": f"Sent #{ticket.id} to {factory}.",
    }


def tool_spec() -> ToolSpec:
    factory_list = " or ".join(FACTORY_OPTIONS)
    model_list = ", ".join(MODEL_OPTIONS)
    return ToolSpec(
        name=NODE_NAME,
        instructions=INSTRUCTIONS,
        schemas=(
            {
                "type": "function",
                "name": TOOL_LIST_TICKETS,
                "description": (
                    "How many labeled tickets are waiting, and their titles. "
                    "Call this before choosing one."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "repo": {
                            "type": "string",
                            "description": (
                                "GitHub owner/repo or URL. Defaults to the loaded "
                                "CodeScope remote."
                            ),
                        },
                        "label": {
                            "type": "string",
                            "description": (
                                f"Issue label to list. Defaults to {DEFAULT_LABEL}."
                            ),
                        },
                    },
                },
            },
            {
                "type": "function",
                "name": TOOL_CHOOSE_TICKET,
                "description": "Select a ticket from the last list by number or title.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "ticket": {
                            "type": "string",
                            "description": "Issue number or exact title.",
                        }
                    },
                    "required": ["ticket"],
                },
            },
            {
                "type": "function",
                "name": TOOL_SET_DISPATCH,
                "description": (
                    f"Where to send the chosen ticket: {factory_list}, and which "
                    f"model ({model_list})."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "factory": {
                            "type": "string",
                            "description": f"{factory_list}.",
                        },
                        "model": {
                            "type": "string",
                            "description": f"Model id. One of {model_list}.",
                        },
                    },
                },
            },
            {
                "type": "function",
                "name": TOOL_SEND_TICKET,
                "description": (
                    "Dispatch the chosen ticket to the selected factory. Confirm "
                    "with the user first."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        ),
        handlers={
            TOOL_LIST_TICKETS: handle_list_tickets,
            TOOL_CHOOSE_TICKET: handle_choose_ticket,
            TOOL_SET_DISPATCH: handle_set_dispatch,
            TOOL_SEND_TICKET: handle_send_ticket,
        },
    )
