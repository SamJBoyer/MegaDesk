"""AgentHandler: AGENTHANDLER:<GUID> → WORKORDER lookup → work graph → FINISHED:<REPO>.

Connects to the factory Redis bus, opens the audit trail, and hands both to the
LangGraph work graph in ``AgentHandler.graph``. The sandbox clones its own repo
and opens a PR; agent MegaDesk uses the Redis sidecar via ``REDIS_URL``.

``run_agent`` stays here because it is the single place the Cursor SDK is
driven; every agent node in the graph calls back into it.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from collections.abc import Sequence
from typing import Any

from cursor_sdk import Agent, CursorAgentError, LocalAgentOptions
from megadesk_contracts import redis_connect, resolve_ephemeral_db, resolve_factory_redis_url
from megadesk_contracts.factory import prompt_payload
from megadesk_contracts.agent_audit import AgentAuditLog
from megadesk_contracts.wire.graph import WORK_GRAPH
from megadesk_contracts.wire.machine import (
    DEFAULT_STARTING_REF,
    STATUS_ERROR,
    agent_handler_fields,
    finished_fields,
    finished_stream,
)
from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

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
    pictures: Sequence[str] = (),
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
            run = agent.send(prompt_payload(instruction, pictures))
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
    status: str,
    pr_url: str,
    handler_key: str,
) -> None:
    """XADD FINISHED:<repo> and delete the AGENTHANDLER hash."""
    stream = finished_stream(repo)
    payload = finished_fields(
        ticket_name=ticket_name,
        ticket_id=ticket_id,
        status=status,
        pr_url=pr_url,
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
    """One-shot worker: read the environment, then run the work graph once."""

    def __init__(self) -> None:
        self.redis_url = resolve_factory_redis_url()
        self.api_key = _env("CURSOR_API_KEY")
        self.workspace = os.environ.get("WORKSPACE", "/workspace")
        self.repo = os.environ.get("REPO_NAME", "unknown")
        self.repo_url = os.environ.get("REPO_URL", "")
        self.ticket = os.environ.get("TICKET", "")
        self.guid = _env("GUID")
        self.model = os.environ.get("CURSOR_MODEL", "")
        self.starting_ref = os.environ.get("STARTING_REF", DEFAULT_STARTING_REF)
        self.auto_pr = os.environ.get("AUTO_PR", "true").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self.env_ticket_id = os.environ.get("TICKET_ID", "")

    def run_once(self) -> int:
        redis = connect_redis(self.redis_url)
        audit = AgentAuditLog.for_run(self.guid, repo=self.repo, ticket=self.ticket)
        try:
            audit.header()
            audit.event("handler-start", f"guid={self.guid} cwd={self.workspace}")
            final = self.run_graph(redis, audit)
            return int(final.get("exit_code", 1))
        finally:
            audit.close()

    def run_graph(self, redis: Redis, audit: AgentAuditLog) -> dict[str, Any]:
        """Assemble this run's context and hand it to the graph."""
        from AgentHandler.graph import GraphReporter, RunContext, run_work_graph

        reporter = GraphReporter(
            redis,
            guid=self.guid,
            spec=WORK_GRAPH,
            audit=audit,
            ticket_id=self.env_ticket_id,
            ticket_name=self.ticket,
            repo=self.repo,
        )
        context = RunContext(
            guid=self.guid,
            redis=redis,
            audit=audit,
            reporter=reporter,
            api_key=self.api_key,
            workspace=self.workspace,
            ticket=self.ticket,
            repo=self.repo,
            repo_url=self.repo_url,
            starting_ref=self.starting_ref,
            auto_pr=self.auto_pr,
            env_ticket_id=self.env_ticket_id,
            default_model=self.model,
        )
        return run_work_graph(context)


def _configure_logging() -> None:
    """Log to container stdout (docker logs) and a durable workspace file."""
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
            "Could not open agent-handler log at %s: %s",
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
