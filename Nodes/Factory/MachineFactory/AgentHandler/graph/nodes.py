"""The nodes of the work graph.

Each node takes the merged state and returns only the keys it changed. A node
that fails sets ``error`` and ``failed_node`` rather than raising: the graph
routes on that field, and an exception would skip teardown, leaving the
AGENTHANDLER hash behind for the manager's reaper to clean up 30 seconds later.

Every node is a plain function of ``(context, state)`` curried into a one-arg
callable by ``build``. That keeps them directly testable without a graph.

Startup clones the target repo into ``/workspace``. Teardown pushes the branch,
opens a PR when ``auto_pr`` is set, and publishes FINISHED with the PR URL.
"""

from __future__ import annotations

import logging
import subprocess
from typing import Any

from megadesk_contracts.wire.machine import (
    DEFAULT_MODEL,
    STATUS_ERROR,
    STATUS_FINISHED,
    STATUS_RUNNING,
    agent_handler_key,
    load_workorder,
    normalize_status,
    parse_agent_handler,
)

from AgentHandler.graph import prompts
from AgentHandler.graph.state import RunContext, WorkState

log = logging.getLogger("agent_handler.graph")

_GIT_TIMEOUT_SEC = 60
_REPORT_LIMIT = 4000


def _fail(node: str, message: str) -> WorkState:
    log.error("%s: %s", node, message)
    return {"error": message, "failed_node": node}


def _clip(text: Any, limit: int = _REPORT_LIMIT) -> str:
    body = str(text or "").strip()
    if len(body) <= limit:
        return body
    return body[: limit - 3] + "..."


def startup_node(context: RunContext, state: WorkState) -> WorkState:
    """Read the handshake, load the order, clone the repo, mark the run running."""
    node = "startup_node"
    context.reporter.node_started(node)

    seed: WorkState = {
        "guid": context.guid,
        "repo": context.repo,
        "ticket_name": context.ticket,
        "ticket_id": context.env_ticket_id,
        "auto_pr": context.auto_pr,
        "pr_url": "",
    }

    key = agent_handler_key(context.guid)
    try:
        data = context.redis.hgetall(key)
    except Exception as exc:  # noqa: BLE001
        context.reporter.node_failed(node, str(exc))
        return {**seed, **_fail(node, f"could not read {key}: {exc}")}

    if not data:
        message = f"hash {key} is empty or missing"
        context.reporter.node_failed(node, message)
        return {**seed, **_fail(node, message)}

    try:
        parsed = parse_agent_handler(data)
        ticket_id = parsed["ticket_id"]
        order = load_workorder(context.redis, ticket_id)
    except (LookupError, ValueError) as exc:
        context.reporter.node_failed(node, str(exc))
        return {**seed, **_fail(node, str(exc))}

    ticket_name = order["ticket_name"]
    repo = order["repo"] or context.repo
    model = order["model"] or context.default_model or DEFAULT_MODEL
    auto_pr = bool(order.get("auto_pr", context.auto_pr))
    repo_url = order.get("URL") or context.repo_url

    context.audit.repo = repo
    context.audit.ticket = ticket_name
    context.audit.model = model
    context.reporter.ticket_id = ticket_id
    context.reporter.ticket_name = ticket_name
    context.reporter.repo = repo
    context.repo_url = repo_url
    context.ticket = ticket_name
    context.auto_pr = auto_pr

    resolved: WorkState = {
        **seed,
        "ticket_id": ticket_id,
        "ticket_name": ticket_name,
        "repo": repo,
        "model": model,
        "instructions": order["instructions"],
        "pictures": list(order.get("pictures") or []),
        "auto_pr": auto_pr,
    }

    if not repo_url:
        message = "Missing REPO_URL / WORKORDER.URL for sandbox clone"
        context.reporter.node_failed(node, message)
        return {**resolved, **_fail(node, message)}

    try:
        _set_handler_status(context, ticket_id=ticket_id, status=STATUS_RUNNING)
    except Exception as exc:  # noqa: BLE001
        context.reporter.node_failed(node, str(exc))
        return {**resolved, **_fail(node, f"could not mark run running: {exc}")}

    try:
        context.prepare_git()
    except (FileNotFoundError, RuntimeError, OSError) as exc:
        message = f"sandbox clone failed: {exc}"
        context.reporter.node_failed(node, _clip(message, 200))
        return {**resolved, **_fail(node, message)}

    context.reporter.node_finished(node, f"ticket={ticket_name} model={model}")
    return resolved


def pathfinder_node(context: RunContext, state: WorkState) -> WorkState:
    node = "pathfinder_node"
    context.reporter.node_started(node)
    instruction = prompts.pathfinder_prompt(
        ticket_name=state.get("ticket_name", ""),
        instructions=state.get("instructions", ""),
    )
    outcome = _run_agent(context, state, instruction, node)
    if outcome.get("error"):
        detail = _clip(outcome["error"], 200)
        context.reporter.node_finished(node, f"survey skipped: {detail}")
        return {"pathfinder_report": ""}

    report = _clip(outcome.get("result"))
    context.reporter.node_finished(node, _clip(report, 200) or "no findings")
    return {"pathfinder_report": report}


def workhorse_node(context: RunContext, state: WorkState) -> WorkState:
    node = "workhorse_node"
    context.reporter.node_started(node)
    pictures = list(state.get("pictures") or [])
    instruction = prompts.workhorse_prompt(
        ticket_name=state.get("ticket_name", ""),
        instructions=state.get("instructions", ""),
        pathfinder_report=state.get("pathfinder_report", ""),
        pictures=pictures,
    )
    outcome = _run_agent(context, state, instruction, node, pictures=pictures)
    if outcome.get("error"):
        context.reporter.node_failed(node, _clip(outcome["error"], 200))
        return _fail(node, outcome["error"])

    report = _clip(outcome.get("result"))
    context.reporter.node_finished(node, _clip(report, 200) or "no summary")
    return {"work_report": report}


def git_node(context: RunContext, state: WorkState) -> WorkState:
    """Read the diff the workhorse left, then commit it."""
    node = "git_node"
    context.reporter.node_started(node)

    status_text = _git(context, "status", "--porcelain")
    if not status_text.strip():
        context.reporter.node_finished(node, "clean tree, nothing to commit")
        return {"diff_summary": ""}
    diff_text = _git(context, "diff")
    stat_text = _git(context, "diff", "--stat")

    instruction = prompts.git_prompt(
        ticket_name=state.get("ticket_name", ""),
        ticket_id=state.get("ticket_id", ""),
        repo=state.get("repo", ""),
        instructions=state.get("instructions", ""),
        status_text=status_text,
        diff_text=diff_text,
    )
    outcome = _run_agent(context, state, instruction, node)
    if outcome.get("error"):
        context.reporter.node_failed(node, _clip(outcome["error"], 200))
        return {"diff_summary": _clip(stat_text), **_fail(node, outcome["error"])}

    sha, message = _read_head(context, state)
    context.reporter.node_finished(node, f"commit={sha or 'none'}")
    return {
        "diff_summary": _clip(stat_text),
        "commit_sha": sha,
        "commit_message": message,
    }


def teardown_node(context: RunContext, state: WorkState) -> WorkState:
    """Push/PR when healthy, publish the outcome, and stop."""
    from AgentHandler.handler import publish_finished

    node = "teardown_node"
    context.reporter.node_started(node)

    error = state.get("error", "")
    status = STATUS_ERROR if error else STATUS_FINISHED
    ticket_id = state.get("ticket_id") or context.env_ticket_id or "missing"
    ticket_name = state.get("ticket_name") or context.ticket or "unknown"
    repo = state.get("repo") or context.repo
    pr_url = state.get("pr_url") or ""

    if status == STATUS_FINISHED and bool(state.get("auto_pr", context.auto_pr)):
        try:
            pr_url = context.publish_branch() or pr_url
        except Exception as exc:  # noqa: BLE001
            error = f"publish failed: {exc}"
            status = STATUS_ERROR
            log.error("%s", error)

    try:
        _set_handler_status(
            context, ticket_id=ticket_id, status=status, error=error
        )
    except Exception as exc:  # noqa: BLE001
        log.error("Could not write final AGENTHANDLER status: %s", exc)

    published = False
    try:
        publish_finished(
            context.redis,
            repo=repo,
            ticket_name=ticket_name,
            ticket_id=ticket_id,
            status=status,
            pr_url=pr_url,
            handler_key=agent_handler_key(context.guid),
        )
        published = True
    except Exception as exc:  # noqa: BLE001
        log.error("Could not publish FINISHED: %s", exc)

    detail = "published FINISHED" if published else "no FINISHED published"
    if pr_url:
        detail = f"pr={pr_url}, {detail}"
    context.reporter.node_finished(node, detail)
    context.reporter.finish_run(status, error)
    context.reporter.clear()

    return {
        "status": status,
        "exit_code": 0 if status == STATUS_FINISHED else 1,
        "pr_url": pr_url,
        "error": error,
    }


def _set_handler_status(
    context: RunContext,
    *,
    ticket_id: str,
    status: str,
    error: str = "",
) -> None:
    from AgentHandler.handler import set_agent_handler_status

    set_agent_handler_status(
        context.redis,
        agent_handler_key(context.guid),
        ticket_id=ticket_id,
        status=status,
        error=error,
    )


def _run_agent(
    context: RunContext,
    state: WorkState,
    instruction: str,
    node: str,
    pictures: list[str] | None = None,
) -> dict[str, Any]:
    """One agent turn inside the cloned workspace."""
    from AgentHandler.handler import run_agent

    model = state.get("model") or context.default_model or DEFAULT_MODEL
    context.audit.event("graph-agent", f"{node} model={model}")
    outcome = run_agent(
        instruction=instruction,
        cwd=context.workspace,
        api_key=context.api_key,
        model=model,
        audit=context.audit,
        pictures=pictures or (),
    )

    status = normalize_status(outcome.get("status"))
    if status != STATUS_FINISHED:
        return {
            "status": status,
            "error": str(outcome.get("error") or f"agent status {status}"),
        }
    return {"status": status, "result": outcome.get("result") or ""}


def _git(context: RunContext, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=context.workspace,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_SEC,
        check=False,
    )
    if result.returncode != 0:
        log.warning("git %s failed: %s", " ".join(args), result.stderr.strip())
        return ""
    return result.stdout


def _read_head(context: RunContext, state: WorkState) -> tuple[str, str]:
    sha = _git(context, "rev-parse", "HEAD").strip()
    message = _git(context, "log", "-1", "--pretty=%B").strip()
    return sha[:12], _clip(message, 1000)
