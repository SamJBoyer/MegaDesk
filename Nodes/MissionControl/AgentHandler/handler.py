"""AgentHandler: AGENTHANDLER:<GUID> → WORKORDER lookup → agent → FINISHED:<REPO>."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

from cursor_sdk import Agent, CursorAgentError, LocalAgentOptions
from dotenv import load_dotenv
from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from AgentHandler.worktree_bind import WorktreeGitBind
from redis_packets import (
    DEFAULT_MODEL,
    WORKORDER_STREAM,
    finished_fields,
    finished_stream,
    agent_handler_fields,
    agent_handler_key,
    load_workorder,
    parse_agent_handler,
)

# Load MissionControl/.env when present (does not override existing env vars).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env", override=False)

log = logging.getLogger("agent_handler")

DEFAULT_REDIS_URL = "redis://localhost:6379/0"


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or value == "":
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def connect_redis(redis_url: str) -> Redis:
    """Connect to an existing Redis server, or raise with a clear error."""
    client = Redis.from_url(
        redis_url,
        decode_responses=True,
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


def run_agent(instruction: str, cwd: str, api_key: str, model: str) -> dict[str, Any]:
    """Create a local Cursor agent bound to cwd and wait for completion."""
    log.info("Starting agent model=%s cwd=%s", model, cwd)
    try:
        with Agent.create(
            model=model,
            api_key=api_key,
            local=LocalAgentOptions(cwd=cwd),
        ) as agent:
            agent_id = getattr(agent, "agent_id", None) or getattr(agent, "agentId", None)
            log.info("Agent created id=%s", agent_id)
            run = agent.send(instruction)
            run_id = getattr(run, "id", None)
            log.info("Run started id=%s", run_id)
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
            log.info(
                "Agent finished model=%s status=%s agent_id=%s run_id=%s result=%s",
                model,
                outcome["status"],
                agent_id,
                run_id,
                result_preview or "(empty)",
            )
            return outcome
    except CursorAgentError as err:
        log.error(
            "Agent startup failed model=%s: %s (retryable=%s)",
            model,
            err.message,
            getattr(err, "is_retryable", None),
        )
        return {
            "status": "error",
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
        self.redis_url = os.environ.get("REDIS_URL", DEFAULT_REDIS_URL)
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

        log.info(
            "AgentHandler one-shot key=%s repo=%s ticket=%s cwd=%s",
            key,
            self.repo,
            self.ticket,
            self.workspace,
        )

        data = redis.hgetall(key)
        if not data:
            log.error("Hash %s is empty or missing", key)
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
            set_agent_handler_status(
                redis,
                key,
                ticket_id=self.env_ticket_id or "missing",
                status="error",
                error=str(exc),
            )
            return 1

        ticket_id = parsed["ticket_id"]
        try:
            order = load_workorder(redis, ticket_id)
        except LookupError as exc:
            log.error("%s", exc)
            set_agent_handler_status(
                redis,
                key,
                ticket_id=ticket_id,
                status="error",
                error=str(exc),
            )
            return 1
        except ValueError as exc:
            log.error("Bad WORKORDER %s: %s", ticket_id, exc)
            set_agent_handler_status(
                redis,
                key,
                ticket_id=ticket_id,
                status="error",
                error=str(exc),
            )
            return 1

        ticket_name = order["ticket_name"]
        instruction = order["instructions"]
        model = order["model"] or os.environ.get("CURSOR_MODEL", DEFAULT_MODEL) or DEFAULT_MODEL
        repo = order["repo"] or self.repo

        # Prefer host paths from env (set by MissionControlManager); fall back to WORKORDER.wt.
        wt = self.host_wt or order.get("wt") or ""
        agent_dir = self.host_agent_dir
        if not wt or not agent_dir:
            err = "Missing HOST_WT / HOST_AGENT_DIR for FINISHED publish"
            log.error("%s", err)
            set_agent_handler_status(
                redis, key, ticket_id=ticket_id, status="error", error=err
            )
            return 1

        set_agent_handler_status(redis, key, ticket_id=ticket_id, status="running")

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
                )
        except (FileNotFoundError, RuntimeError, OSError) as exc:
            log.error("Worktree git bind failed: %s", exc)
            outcome = {
                "status": "error",
                "error": f"worktree git bind failed: {exc}",
            }

        status = str(outcome.get("status") or "unknown")
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

        if status == "finished":
            log.info(
                "Task done guid=%s ticket_id=%s WORKORDER=%s",
                self.guid,
                ticket_id,
                WORKORDER_STREAM,
            )
            return 0

        log.warning(
            "Task finished with status=%s error=%s guid=%s ticket_id=%s",
            status,
            _truncate(error) if error else "(none)",
            self.guid,
            ticket_id,
        )
        return 1


def _configure_logging() -> None:
    """Log to container stdout (docker logs) and a durable worktree file."""
    level = os.environ.get("LOG_LEVEL", "INFO")
    fmt = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
    logging.basicConfig(level=level, format=fmt, stream=sys.stdout)

    workspace = os.environ.get("WORKSPACE", "/workspace")
    log_dir = Path(workspace) / ".mission_control"
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
