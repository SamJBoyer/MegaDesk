"""AgentHandler: AGENTHANDLER:<GUID> → WORKORDER lookup → agent → FINISHED:<REPO>."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

from cursor_sdk import Agent, CursorAgentError, LocalAgentOptions
from megadesk_contracts import redis_connect, resolve_ephemeral_db, resolve_factory_redis_url
from megadesk_contracts.agent_audit import AgentAuditLog
from megadesk_contracts.wire.machine import (
    DEFAULT_MODEL,
    STATUS_ERROR,
    STATUS_FINISHED,
    STATUS_RUNNING,
    agent_handler_fields,
    agent_handler_key,
    finished_fields,
    finished_stream,
    load_workorder,
    normalize_status,
    parse_agent_handler,
)
from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from AgentHandler.worktree_bind import WorktreeGitBind

log = logging.getLogger("agent_handler")


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or value == "":
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def connect_redis(redis_url: str) -> Redis:
    """Connect to an existing Redis server, or raise with a clear error."""
    client = redis_connect(
        redis_url,
        db=resolve_ephemeral_db(redis_url),
        socket_connect_timeout=2,
        socket_timeout=None,
    )
    try:
        client.ping()
    except (RedisConnectionError, RedisTimeoutError, OSError) as exc:
        raise SystemExit(
            f"Failed to connect to Redis at {redis_url}. "
            "Start a local Redis server and retry."
        ) from exc
    return client


def _with_commit_guidance(instruction: str, *, repo: str, ticket: str) -> str:
    """Append commit-message requirements for the agent."""
    work_name = ticket or repo or "unknown"
    lines = [
        instruction.rstrip(),
        "",
        "When you commit your work, write a detailed commit message that:",
        f"- Names the ticket/work: {work_name}",
        f"- Includes the ticket name: {ticket or '(none provided)'}",
        "- Includes the original instructions for this task (summarize if very long)",
        "- Describes the work done to implement the feature in enough detail for a reviewer",
    ]
    return "\n".join(lines)


_RESULT_LOG_MAX = 2000


def _truncate(text: str, limit: int = _RESULT_LOG_MAX) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _stream_run(run: Any, audit: AgentAuditLog) -> None:
    """Drain SDK messages into the audit log. wait() is still required after this.

    The stream is how we know the agent is moving; a silent wait() is what made
    a stuck tool call indistinguishable from a long think.
    """
    stream = getattr(run, "messages", None) or getattr(run, "stream", None)
    if stream is None:
        return
    supports = getattr(run, "supports", None)
    if callable(supports) and not supports("stream"):
        reason = getattr(run, "unsupported_reason", lambda _op: "")("stream")
        audit.event("stream-unavailable", str(reason) if reason else "run does not support stream")
        return
    try:
        for message in stream():
            audit.sdk_message(message)
    except Exception as exc:  # noqa: BLE001
        log.warning("Audit stream interrupted: %s", exc)
        audit.event("stream-error", str(exc))


def run_agent(
    instruction: str,
    cwd: str,
    api_key: str,
    model: str,
    audit: AgentAuditLog | None = None,
) -> dict[str, Any]:
    """Create a local Cursor agent bound to cwd and wait for completion."""
    trail = audit or AgentAuditLog(guid="unknown")
    trail.event("starting", f"model={model} cwd={cwd}")
    try:
        with Agent.create(
            model=model,
            api_key=api_key,
            local=LocalAgentOptions(cwd=cwd),
        ) as agent:
            agent_id = getattr(agent, "agent_id", None) or getattr(agent, "agentId", None)
            trail.event("created", f"agent_id={agent_id}")
            run = agent.send(instruction)
            run_id = getattr(run, "id", None)
            trail.event("run", f"run_id={run_id}")
            _stream_run(run, trail)
            result = run.wait()
            status = getattr(result, "status", None) or "unknown"
            text = getattr(result, "result", None)
            if text is None and hasattr(run, "text"):
                try:
                    text = run.text()
                except Exception:  # noqa: BLE001
                    text = None
            outcome = {
                "status": str(status),
                "agent_id": agent_id,
                "run_id": run_id,
                "result": text if text is None else str(text),
            }
            result_preview = _truncate(str(text)) if text else ""
            trail.event(
                "agent-finished",
                f"status={outcome['status']} agent_id={agent_id} run_id={run_id}",
            )
            if result_preview:
                trail.event("result", result_preview)
            return outcome
    except CursorAgentError as err:
        trail.event(
            "startup-failed",
            f"{err.message} retryable={getattr(err, 'is_retryable', None)}",
        )
        log.error(
            "Agent startup failed model=%s: %s (retryable=%s)",
            model,
            err.message,
            getattr(err, "is_retryable", None),
        )
        return {
            "status": STATUS_ERROR,
            "error": err.message,
            "retryable": bool(getattr(err, "is_retryable", False)),
        }


def publish_finished(
    redis: Redis,
    *,
    repo: str,
    ticket_name: str,
    ticket_id: str,
    wt: str,
    agent_dir: str,
    handler_key: str,
) -> None:
    """XADD FINISHED:<repo> and delete the AGENTHANDLER hash."""
    stream = finished_stream(repo)
    payload = finished_fields(
        ticket_name=ticket_name,
        ticket_id=ticket_id,
        wt=wt,
        agent_dir=agent_dir,
    )
    entry_id = redis.xadd(stream, payload)
    redis.delete(handler_key)
    log.info("Pushed %s id=%s and deleted %s", stream, entry_id, handler_key)


def set_agent_handler_status(
    redis: Redis,
    key: str,
    *,
    ticket_id: str,
    status: str,
    error: str = "",
) -> None:
    redis.hset(
        key,
        mapping=agent_handler_fields(ticket_id=ticket_id, status=status, error=error),
    )


class AgentHandler:
    """One-shot worker: resolve WORKORDER via AGENTHANDLER ticket_id, run agent, finish."""

    def __init__(self) -> None:
        self.redis_url = resolve_factory_redis_url()
        self.api_key = _env("CURSOR_API_KEY")
        self.workspace = os.environ.get("WORKSPACE", "/workspace")
        self.repo = os.environ.get("REPO_NAME", "unknown")
        self.ticket = os.environ.get("TICKET", "")
        self.guid = _env("GUID")
        # Host absolute paths for FINISHED (MergeManager consumes these).
        self.host_wt = os.environ.get("HOST_WT", "")
        self.host_agent_dir = os.environ.get("HOST_AGENT_DIR", "")
        self.env_ticket_id = os.environ.get("TICKET_ID", "")

    def run_once(self) -> int:
        key = agent_handler_key(self.guid)
        redis = connect_redis(self.redis_url)
        audit = AgentAuditLog.for_run(
            self.guid, repo=self.repo, ticket=self.ticket
        )
        try:
            return self._run_once(redis, key, audit)
        finally:
            audit.close()

    def _run_once(self, redis: Redis, key: str, audit: AgentAuditLog) -> int:
        audit.header()
        audit.event(
            "handler-start",
            f"key={key} cwd={self.workspace}",
        )

        data = redis.hgetall(key)
        if not data:
            log.error("Hash %s is empty or missing", key)
            audit.event("error", f"hash {key} is empty or missing")
            ticket_id = self.env_ticket_id or "missing"
            ticket_name = self.ticket or "unknown"
            if self.host_wt and self.host_agent_dir:
                publish_finished(
                    redis,
                    repo=self.repo,
                    ticket_name=ticket_name,
                    ticket_id=ticket_id,
                    wt=self.host_wt,
                    agent_dir=self.host_agent_dir,
                    handler_key=key,
                )
            return 1

        try:
            parsed = parse_agent_handler(data)
        except ValueError as exc:
            log.error("%s", exc)
            audit.event("error", str(exc))
            set_agent_handler_status(
                redis,
                key,
                ticket_id=self.env_ticket_id or "missing",
                status=STATUS_ERROR,
                error=str(exc),
            )
            return 1

        ticket_id = parsed["ticket_id"]
        try:
            order = load_workorder(redis, ticket_id)
        except LookupError as exc:
            log.error("%s", exc)
            audit.event("error", str(exc))
            set_agent_handler_status(
                redis,
                key,
                ticket_id=ticket_id,
                status=STATUS_ERROR,
                error=str(exc),
            )
            return 1
        except ValueError as exc:
            log.error("Bad WORKORDER %s: %s", ticket_id, exc)
            audit.event("error", f"bad WORKORDER {ticket_id}: {exc}")
            set_agent_handler_status(
                redis,
                key,
                ticket_id=ticket_id,
                status=STATUS_ERROR,
                error=str(exc),
            )
            return 1

        ticket_name = order["ticket_name"]
        instruction = order["instructions"]
        model = order["model"] or os.environ.get("CURSOR_MODEL", DEFAULT_MODEL) or DEFAULT_MODEL
        repo = order["repo"] or self.repo
        audit.repo = repo
        audit.ticket = ticket_name
        audit.model = model
        audit.event(
            "order",
            f"ticket_id={ticket_id} ticket={ticket_name} model={model}",
        )

        # Prefer host paths from env (set by MachineFactoryManager); fall back to WORKORDER.wt.
        wt = self.host_wt or order.get("wt") or ""
        agent_dir = self.host_agent_dir
        if not wt or not agent_dir:
            err = "Missing HOST_WT / HOST_AGENT_DIR for FINISHED publish"
            log.error("%s", err)
            audit.event("error", err)
            set_agent_handler_status(
                redis, key, ticket_id=ticket_id, status=STATUS_ERROR, error=err
            )
            return 1

        set_agent_handler_status(redis, key, ticket_id=ticket_id, status=STATUS_RUNNING)

        guided = _with_commit_guidance(instruction, repo=repo, ticket=ticket_name)
        bare_mount = os.environ.get("BARE_MOUNT", "/bare")
        try:
            with WorktreeGitBind(
                self.workspace,
                bare_mount=bare_mount,
                ticket=self.ticket or ticket_name,
            ):
                outcome = run_agent(
                    instruction=guided,
                    cwd=self.workspace,
                    api_key=self.api_key,
                    model=model,
                    audit=audit,
                )
        except (FileNotFoundError, RuntimeError, OSError) as exc:
            log.error("Worktree git bind failed: %s", exc)
            audit.event("error", f"worktree git bind failed: {exc}")
            outcome = {
                "status": STATUS_ERROR,
                "error": f"worktree git bind failed: {exc}",
            }

        # The SDK's spelling is not ours, and the hash only accepts ours.
        status = normalize_status(outcome.get("status"))
        error = str(outcome.get("error") or "")
        set_agent_handler_status(
            redis,
            key,
            ticket_id=ticket_id,
            status=status,
            error=error,
        )

        publish_finished(
            redis,
            repo=repo,
            ticket_name=ticket_name,
            ticket_id=ticket_id,
            wt=wt,
            agent_dir=agent_dir,
            handler_key=key,
        )

        if status == STATUS_FINISHED:
            audit.event("done", f"ticket_id={ticket_id}")
            return 0

        audit.event(
            "failed",
            f"status={status} error={_truncate(error) if error else '(none)'}",
        )
        return 1


def _configure_logging() -> None:
    """Log to container stdout (docker logs) and a durable worktree file."""
    level = os.environ.get("LOG_LEVEL", "INFO")
    fmt = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
    logging.basicConfig(level=level, format=fmt, stream=sys.stdout)

    workspace = os.environ.get("WORKSPACE", "/workspace")
    log_dir = Path(workspace) / ".machine_factory"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(
            log_dir / "agent_handler.log",
            encoding="utf-8",
        )
        file_handler.setFormatter(logging.Formatter(fmt))
        logging.getLogger().addHandler(file_handler)
    except OSError as exc:
        logging.getLogger("agent_handler").warning(
            "Could not open worktree agent-handler log at %s: %s",
            log_dir / "agent_handler.log",
            exc,
        )


def main() -> None:
    _configure_logging()
    try:
        code = AgentHandler().run_once()
    except RuntimeError as exc:
        log.error("%s", exc)
        raise SystemExit(1) from exc
    raise SystemExit(code)


if __name__ == "__main__":
    main()
