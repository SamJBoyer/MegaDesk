"""The five nodes of the work graph.

Each node takes the merged state and returns only the keys it changed. A node
that fails sets ``error`` and ``failed_node`` rather than raising: the graph
routes on that field, and an exception would skip teardown, leaving the
AGENTHANDLER hash behind for the manager's reaper to clean up 30 seconds later.

Every node is a plain function of ``(context, state)`` curried into a one-arg
callable by ``build``. That keeps them directly testable without a graph.
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


# --- startup ---------------------------------------------------------------


def startup_node(context: RunContext, state: WorkState) -> WorkState:
    """Read the handshake, load the order, and mark the run running.

    Host paths are seeded from the environment before anything can fail, so a
    missing or malformed hash still leaves teardown enough to publish FINISHED
    and let MergeManager see the worktree.
    """
    node = "startup_node"
    context.reporter.node_started(node)

    seed: WorkState = {
        "guid": context.guid,
        "repo": context.repo,
        "ticket_name": context.ticket,
        "wt": context.host_wt,
        "agent_dir": context.host_agent_dir,
        "ticket_id": context.env_ticket_id,
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
    wt = context.host_wt or order.get("wt") or ""
    agent_dir = context.host_agent_dir

    context.audit.repo = repo
    context.audit.ticket = ticket_name
    context.audit.model = model
    context.reporter.ticket_id = ticket_id
    context.reporter.ticket_name = ticket_name
    context.reporter.repo = repo

    resolved: WorkState = {
        **seed,
        "ticket_id": ticket_id,
        "ticket_name": ticket_name,
        "repo": repo,
        "model": model,
        "instructions": order["instructions"],
        "wt": wt,
        "agent_dir": agent_dir,
    }

    if not wt or not agent_dir:
        message = "Missing HOST_WT / HOST_AGENT_DIR for FINISHED publish"
        context.reporter.node_failed(node, message)
        return {**resolved, **_fail(node, message)}

    try:
        _set_handler_status(context, ticket_id=ticket_id, status=STATUS_RUNNING)
    except Exception as exc:  # noqa: BLE001
        context.reporter.node_failed(node, str(exc))
        return {**resolved, **_fail(node, f"could not mark run running: {exc}")}

    context.reporter.node_finished(node, f"ticket={ticket_name} model={model}")
    return resolved


# --- agent nodes -----------------------------------------------------------


def pathfinder_node(context: RunContext, state: WorkState) -> WorkState:
    node = "pathfinder_node"
    context.reporter.node_started(node)
    instruction = prompts.pathfinder_prompt(
        ticket_name=state.get("ticket_name", ""),
        instructions=state.get("instructions", ""),
    )
    outcome = _run_agent(context, state, instruction, node)
    if outcome.get("error"):
        # A survey that fell over is not worth stopping the run for; the
        # workhorse can read the repo itself. Record it and carry on.
        detail = _clip(outcome["error"], 200)
        context.reporter.node_finished(node, f"survey skipped: {detail}")
        return {"pathfinder_report": ""}

    report = _clip(outcome.get("result"))
    context.reporter.node_finished(node, _clip(report, 200) or "no findings")
    return {"pathfinder_report": report}


def workhorse_node(context: RunContext, state: WorkState) -> WorkState:
    node = "workhorse_node"
    context.reporter.node_started(node)
    instruction = prompts.workhorse_prompt(
        ticket_name=state.get("ticket_name", ""),
        instructions=state.get("instructions", ""),
        pathfinder_report=state.get("pathfinder_report", ""),
    )
    outcome = _run_agent(context, state, instruction, node)
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

    try:
        with context.git_bind(state.get("ticket_name", "")):
            status_text = _git(context, "status", "--porcelain")
            if not status_text.strip():
                context.reporter.node_finished(node, "clean tree, nothing to commit")
                return {"diff_summary": ""}
            diff_text = _git(context, "diff")
            stat_text = _git(context, "diff", "--stat")
    except (FileNotFoundError, RuntimeError, OSError) as exc:
        message = f"worktree git bind failed: {exc}"
        context.reporter.node_failed(node, _clip(message, 200))
        return _fail(node, message)

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


# --- teardown --------------------------------------------------------------


def teardown_node(context: RunContext, state: WorkState) -> WorkState:
    """Publish the outcome, drop the run keys, and let the container exit.

    Reached from every other node, so it must work with whatever state it is
    handed — including a run that failed before it knew its own ticket.
    """
    from AgentHandler.handler import publish_finished

    node = "teardown_node"
    context.reporter.node_started(node)

    error = state.get("error", "")
    status = STATUS_ERROR if error else STATUS_FINISHED
    ticket_id = state.get("ticket_id") or context.env_ticket_id or "missing"
    ticket_name = state.get("ticket_name") or context.ticket or "unknown"
    repo = state.get("repo") or context.repo
    wt = state.get("wt") or context.host_wt
    agent_dir = state.get("agent_dir") or context.host_agent_dir

    try:
        _set_handler_status(
            context, ticket_id=ticket_id, status=status, error=error
        )
    except Exception as exc:  # noqa: BLE001
        log.error("Could not write final AGENTHANDLER status: %s", exc)

    published = False
    if wt and agent_dir:
        try:
            publish_finished(
                context.redis,
                repo=repo,
                ticket_name=ticket_name,
                ticket_id=ticket_id,
                wt=wt,
                agent_dir=agent_dir,
                handler_key=agent_handler_key(context.guid),
            )
            published = True
        except Exception as exc:  # noqa: BLE001
            log.error("Could not publish FINISHED: %s", exc)
    else:
        log.error("No worktree paths known; skipping FINISHED publish")

    detail = "published FINISHED" if published else "no FINISHED published"
    context.reporter.node_finished(node, detail)
    context.reporter.finish_run(status, error)
    context.reporter.clear()

    return {"status": status, "exit_code": 0 if status == STATUS_FINISHED else 1}


# --- shared plumbing -------------------------------------------------------


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
) -> dict[str, Any]:
    """One agent turn inside the bound worktree.

    Imported at call time so ``handler`` stays free to import the graph, and so
    a test that patches ``AgentHandler.handler.Agent`` still reaches this path.
    """
    from AgentHandler.handler import run_agent

    model = state.get("model") or context.default_model or DEFAULT_MODEL
    context.audit.event("graph-agent", f"{node} model={model}")
    try:
        with context.git_bind(state.get("ticket_name", "")):
            outcome = run_agent(
                instruction=instruction,
                cwd=context.workspace,
                api_key=context.api_key,
                model=model,
                audit=context.audit,
            )
    except (FileNotFoundError, RuntimeError, OSError) as exc:
        return {"status": STATUS_ERROR, "error": f"worktree git bind failed: {exc}"}

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
    try:
        with context.git_bind(state.get("ticket_name", "")):
            sha = _git(context, "rev-parse", "HEAD").strip()
            message = _git(context, "log", "-1", "--pretty=%B").strip()
    except (FileNotFoundError, RuntimeError, OSError) as exc:
        log.warning("Could not read HEAD after commit: %s", exc)
        return "", ""
    return sha[:12], _clip(message, 1000)
