"""Per-run agent audit transcripts in the Supervisor session Logs folder.

MachineFactory sandboxes used to log only start and finish. That is not enough
to tell whether a run is making progress or stuck inside a tool call. The audit
file is the live trail: one ``Logs/{session}/agent-{guid}.md`` per run, flushed
on every event, never put on a Redis stream.

The sandbox sees a single bind-mounted file (``MEGADESK_AGENT_AUDIT_PATH``), not
the rest of the session directory, so one agent cannot read another node's log.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from megadesk_contracts.log_session import attach_log_session, session_log_path
from megadesk_contracts.paths import ENV_LOGS_DIR

log = logging.getLogger("agent_audit")

ENV_AGENT_AUDIT_PATH = "MEGADESK_AGENT_AUDIT_PATH"
CONTAINER_AUDIT_PATH = "/tmp/megadesk-agent-audit.md"

_SKIP_TYPES = frozenset({"usage", "request"})
_BODY_LIMIT = 800
_THINKING_LIMIT = 400


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _compact(value: Any, *, limit: int = _BODY_LIMIT) -> str:
    if value is None or value == "" or value == {}:
        return ""
    if isinstance(value, str):
        return _truncate(value, limit)
    try:
        dumped = json.dumps(value, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        dumped = str(value)
    return _truncate(dumped, limit)


def _message_type(message: Any) -> str:
    if isinstance(message, Mapping):
        return str(message.get("type") or "")
    return str(getattr(message, "type", "") or "")


def _attr(message: Any, *names: str) -> Any:
    for name in names:
        if isinstance(message, Mapping) and name in message:
            return message[name]
        value = getattr(message, name, None)
        if value is not None:
            return value
    return None


def _content_text(message: Any) -> str:
    inner = _attr(message, "message")
    content = _attr(inner, "content") if inner is not None else None
    if content is None:
        text = _attr(message, "text")
        return str(text) if text else ""
    parts: list[str] = []
    for block in content:
        block_type = _attr(block, "type") or ""
        if block_type == "text" or _attr(block, "text"):
            text = _attr(block, "text")
            if text:
                parts.append(str(text))
        elif block_type in {"tool_use", "tool-use"}:
            name = _attr(block, "name") or "tool"
            args = _compact(_attr(block, "input", "args"), limit=200)
            extra = f" {args}" if args else ""
            parts.append(f"tool_use {name}{extra}")
        elif isinstance(block, Mapping):
            text = block.get("text")
            if text:
                parts.append(str(text))
        elif isinstance(block, str):
            parts.append(block)
    return "\n".join(parts).strip()


def format_sdk_message(message: Any) -> Optional[str]:
    """One markdown event body for an SDK message, or None to skip it.

    Usage and request envelopes are noise for "is it stuck?". Tool calls,
    thinking, assistant text and status changes are the trail.
    """
    kind = _message_type(message)
    if not kind or kind in _SKIP_TYPES:
        return None

    if kind == "tool_call":
        name = _attr(message, "name") or "tool"
        status = _attr(message, "status") or ""
        call_id = _attr(message, "call_id", "callId") or ""
        header = f"**tool_call** `{name}` {status}".rstrip()
        if call_id:
            header += f" `{call_id}`"
        lines = [header]
        args = _compact(_attr(message, "args"))
        if args:
            lines.append(f"args: {args}")
        result = _compact(_attr(message, "result"))
        if result:
            lines.append(f"result: {result}")
        return "\n".join(lines)

    if kind == "thinking":
        text = _truncate(str(_attr(message, "text") or ""), _THINKING_LIMIT)
        duration = _attr(message, "thinking_duration_ms", "thinkingDurationMs")
        header = "**thinking**"
        if duration is not None and duration != "":
            header += f" ({duration}ms)"
        return f"{header}\n{text}" if text else header

    if kind in {"assistant", "user"}:
        body = _truncate(_content_text(message), _BODY_LIMIT)
        if not body:
            return None
        return f"**{kind}**\n{body}"

    if kind == "status":
        status = _attr(message, "status") or ""
        text = _attr(message, "message") or ""
        line = f"**status** {status}".rstrip()
        if text:
            line += f" — {_truncate(str(text), 200)}"
        return line

    if kind == "task":
        status = _attr(message, "status") or ""
        text = _truncate(str(_attr(message, "text") or ""), _BODY_LIMIT)
        line = f"**task** {status}".rstrip()
        return f"{line}\n{text}" if text else line

    if kind == "system":
        subtype = _attr(message, "subtype") or ""
        model = _attr(message, "model")
        extra = f" {subtype}".rstrip()
        if model:
            extra += f" model={model}"
        return f"**system**{extra}".rstrip()

    extra = _compact(
        {
            name: _attr(message, name)
            for name in ("status", "name", "text", "message")
            if _attr(message, name)
        },
        limit=200,
    )
    return f"**{kind}** {extra}".rstrip() if extra else f"**{kind}**"


def agent_audit_stem(guid: str) -> str:
    cleaned = (guid or "unknown").strip() or "unknown"
    return f"agent-{cleaned}"


def ensure_agent_audit_file(guid: str) -> Path:
    """Create ``Logs/{session}/agent-{guid}.md`` so Docker can bind-mount it.

    Docker turns a missing bind target into a directory, so the file must exist
    on the host before ``docker run``.
    """
    attach_log_session()
    path = session_log_path(agent_audit_stem(guid))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    return path


def resolve_audit_path(guid: str) -> Optional[Path]:
    """Where this process should write the audit file.

    Order: explicit ``MEGADESK_AGENT_AUDIT_PATH`` (the sandbox mount), the live
    session folder, then the worktree sidecar AgentHandler already uses.
    """
    explicit = (os.environ.get(ENV_AGENT_AUDIT_PATH) or "").strip()
    if explicit:
        return Path(explicit)
    if (os.environ.get(ENV_LOGS_DIR) or "").strip():
        try:
            return session_log_path(agent_audit_stem(guid))
        except RuntimeError:
            pass
    workspace = (os.environ.get("WORKSPACE") or "").strip()
    if workspace:
        return Path(workspace) / ".machine_factory" / "agent_audit.md"
    return None


def agent_audit_bind_args(guid: str) -> list[str]:
    """``docker run`` fragments that mount this run's audit file into the sandbox."""
    try:
        host_path = ensure_agent_audit_file(guid)
    except (RuntimeError, OSError) as exc:
        log.warning("Agent audit file unavailable for %s: %s", guid, exc)
        return []
    return [
        "-v",
        f"{host_path}:{CONTAINER_AUDIT_PATH}",
        "-e",
        f"{ENV_AGENT_AUDIT_PATH}={CONTAINER_AUDIT_PATH}",
    ]


class AgentAuditLog:
    """Append-only markdown transcript. File writes are best-effort; logger always fires."""

    def __init__(
        self,
        guid: str,
        *,
        path: Optional[Path] = None,
        repo: str = "",
        ticket: str = "",
        model: str = "",
    ) -> None:
        self.guid = guid
        self.repo = repo
        self.ticket = ticket
        self.model = model
        self.path = path if path is not None else resolve_audit_path(guid)
        self._fh: Optional[Any] = None
        if self.path is not None:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._fh = open(self.path, "a", encoding="utf-8", buffering=1)
            except OSError as exc:
                log.warning("Could not open agent audit %s: %s", self.path, exc)
                self._fh = None

    @classmethod
    def for_run(
        cls,
        guid: str,
        *,
        repo: str = "",
        ticket: str = "",
        model: str = "",
    ) -> "AgentAuditLog":
        return cls(guid, repo=repo, ticket=ticket, model=model)

    def header(self) -> None:
        lines = [
            f"# Agent audit `{self.guid}`",
            f"- repo: {self.repo or '(unknown)'}",
            f"- ticket: {self.ticket or '(none)'}",
            f"- model: {self.model or '(unset)'}",
            f"- started: {_utc_stamp()}",
            "",
        ]
        self._write("\n".join(lines), also_log=False)
        log.info(
            "Audit log guid=%s path=%s repo=%s ticket=%s",
            self.guid,
            self.path or "(none)",
            self.repo or "(unknown)",
            self.ticket or "(none)",
        )

    def event(self, kind: str, detail: str = "") -> None:
        body = f"**{kind}**"
        if detail:
            body += f" {detail}"
        self._write(self._wrap(body))

    def sdk_message(self, message: Any) -> None:
        formatted = format_sdk_message(message)
        if formatted is None:
            return
        self._write(self._wrap(formatted))

    def close(self) -> None:
        fh = self._fh
        self._fh = None
        if fh is None:
            return
        try:
            fh.flush()
        except OSError:
            pass
        try:
            fh.close()
        except OSError:
            pass

    def _wrap(self, body: str) -> str:
        return f"## {_utc_stamp()}\n{body}\n"

    def _write(self, text: str, *, also_log: bool = True) -> None:
        if also_log:
            summary = text.strip().splitlines()
            if summary:
                log.info("audit %s", " | ".join(summary[:2]))
        fh = self._fh
        if fh is None:
            return
        try:
            fh.write(text if text.endswith("\n") else text + "\n")
            fh.flush()
        except OSError as exc:
            log.warning("Agent audit write failed %s: %s", self.path, exc)
