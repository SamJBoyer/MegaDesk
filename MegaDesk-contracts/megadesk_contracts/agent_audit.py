"""Per-run agent audit transcripts in the Supervisor session Logs folder.

MachineFactory sandboxes used to log only start and finish. That is not enough
to tell whether a run is making progress or stuck inside a tool call. Two files
per run, flushed on every event, never put on a Redis stream:
``Logs/{session}/agent-{guid}.md`` (pretty) and ``agent-{guid}.tokens.md``
(token-by-token).

The sandbox sees only those two bind-mounted files (``MEGADESK_AGENT_AUDIT_PATH``
and ``MEGADESK_AGENT_AUDIT_TOKENS_PATH``), not the rest of the session directory,
so one agent cannot read another node's log.
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
ENV_AGENT_AUDIT_TOKENS_PATH = "MEGADESK_AGENT_AUDIT_TOKENS_PATH"
CONTAINER_AUDIT_PATH = "/tmp/megadesk-agent-audit.md"
CONTAINER_AUDIT_TOKENS_PATH = "/tmp/megadesk-agent-audit.tokens.md"

_SKIP_TYPES = frozenset({"usage", "request"})
_STREAM_KINDS = frozenset({"thinking", "assistant", "user"})
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


def _content_text(message: Any, *, strip: bool = True) -> str:
    inner = _attr(message, "message")
    content = _attr(inner, "content") if inner is not None else None
    if content is None:
        text = _attr(message, "text")
        raw = str(text) if text else ""
        return raw.strip() if strip else raw
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
    joined = "\n".join(parts)
    return joined.strip() if strip else joined


def _stream_delta(message: Any, kind: str) -> str:
    """Raw token/phrase delta. Do not strip: leading spaces are part of the token."""
    if kind == "thinking":
        value = _attr(message, "text")
        return "" if value is None else str(value)
    return _content_text(message, strip=False)


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


def tokens_path_beside(pretty: Path) -> Path:
    """``agent-guid.md`` → ``agent-guid.tokens.md``."""
    return pretty.with_suffix(".tokens.md")


def ensure_agent_audit_file(guid: str) -> Path:
    """Create ``Logs/{session}/agent-{guid}.md`` and ``.tokens.md`` for Docker binds.

    Docker turns a missing bind target into a directory, so both files must exist
    on the host before ``docker run``.
    """
    attach_log_session()
    path = session_log_path(agent_audit_stem(guid))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    tokens_path_beside(path).touch(exist_ok=True)
    return path


def resolve_audit_path(guid: str) -> Optional[Path]:
    """Where this process should write the pretty audit file.

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


def resolve_tokens_path(guid: str, pretty: Optional[Path] = None) -> Optional[Path]:
    """Where this process should write the token-by-token audit file."""
    explicit = (os.environ.get(ENV_AGENT_AUDIT_TOKENS_PATH) or "").strip()
    if explicit:
        return Path(explicit)
    if pretty is None:
        pretty = resolve_audit_path(guid)
    if pretty is None:
        return None
    return tokens_path_beside(pretty)


def agent_audit_bind_args(guid: str) -> list[str]:
    """``docker run`` fragments that mount this run's pretty and token audit files."""
    try:
        host_path = ensure_agent_audit_file(guid)
    except (RuntimeError, OSError) as exc:
        log.warning("Agent audit file unavailable for %s: %s", guid, exc)
        return []
    tokens_host = tokens_path_beside(host_path)
    return [
        "-v",
        f"{host_path}:{CONTAINER_AUDIT_PATH}",
        "-e",
        f"{ENV_AGENT_AUDIT_PATH}={CONTAINER_AUDIT_PATH}",
        "-v",
        f"{tokens_host}:{CONTAINER_AUDIT_TOKENS_PATH}",
        "-e",
        f"{ENV_AGENT_AUDIT_TOKENS_PATH}={CONTAINER_AUDIT_TOKENS_PATH}",
    ]


class AgentAuditLog:
    """Pretty and token-by-token markdown transcripts. File writes are best-effort."""

    def __init__(
        self,
        guid: str,
        *,
        path: Optional[Path] = None,
        tokens_path: Optional[Path] = None,
        repo: str = "",
        ticket: str = "",
        model: str = "",
    ) -> None:
        self.guid = guid
        self.repo = repo
        self.ticket = ticket
        self.model = model
        self.path = path if path is not None else resolve_audit_path(guid)
        self.tokens_path = (
            tokens_path
            if tokens_path is not None
            else resolve_tokens_path(guid, self.path)
        )
        self._fh: Optional[Any] = None
        self._tokens_fh: Optional[Any] = None
        self._open_kind: Optional[str] = None
        self._stream_tail_ws: bool = True
        self._fh = self._open(self.path)
        self._tokens_fh = self._open(self.tokens_path)

    def _open(self, path: Optional[Path]) -> Optional[Any]:
        if path is None:
            return None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            return open(path, "a", encoding="utf-8", buffering=1)
        except OSError as exc:
            log.warning("Could not open agent audit %s: %s", path, exc)
            return None

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
        stamp = _utc_stamp()
        pretty = [
            f"# Agent audit `{self.guid}`",
            f"- repo: {self.repo or '(unknown)'}",
            f"- ticket: {self.ticket or '(none)'}",
            f"- model: {self.model or '(unset)'}",
            f"- started: {stamp}",
            "",
        ]
        tokens = [
            f"# Agent audit `{self.guid}` (tokens)",
            f"- repo: {self.repo or '(unknown)'}",
            f"- ticket: {self.ticket or '(none)'}",
            f"- model: {self.model or '(unset)'}",
            f"- started: {stamp}",
            "",
        ]
        self._write("\n".join(pretty), also_log=False)
        self._write_tokens("\n".join(tokens))
        log.info(
            "Audit log guid=%s path=%s tokens=%s repo=%s ticket=%s",
            self.guid,
            self.path or "(none)",
            self.tokens_path or "(none)",
            self.repo or "(unknown)",
            self.ticket or "(none)",
        )

    def event(self, kind: str, detail: str = "") -> None:
        self._close_stream()
        body = f"**{kind}**"
        if detail:
            body += f" {detail}"
        wrapped = self._wrap(body)
        self._write(wrapped)
        self._write_tokens(wrapped)

    def sdk_message(self, message: Any) -> None:
        kind = _message_type(message)
        if not kind or kind in _SKIP_TYPES:
            return
        formatted = format_sdk_message(message)
        if formatted is not None:
            wrapped = self._wrap(formatted)
            self._write_tokens(wrapped)
        else:
            wrapped = None
        if kind in _STREAM_KINDS:
            self._sdk_stream(message, kind)
            return
        self._close_stream()
        if wrapped is None:
            return
        self._write(wrapped)

    def close(self) -> None:
        self._close_stream()
        self._close_handle("_fh")
        self._close_handle("_tokens_fh")

    def _close_handle(self, attr: str) -> None:
        fh = getattr(self, attr)
        setattr(self, attr, None)
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

    def _sdk_stream(self, message: Any, kind: str) -> None:
        """Append a thinking/assistant/user delta into the pretty section."""
        delta = _stream_delta(message, kind)
        duration = None
        if kind == "thinking":
            duration = _attr(message, "thinking_duration_ms", "thinkingDurationMs")
        if delta:
            if self._open_kind != kind:
                self._close_stream()
                self._open_kind = kind
                self._stream_tail_ws = True
                self._write(self._wrap(f"**{kind}**"))
            if (
                kind == "thinking"
                and not self._stream_tail_ws
                and not delta[0].isspace()
            ):
                delta = " " + delta
            self._write(delta, also_log=False, ensure_newline=False)
            self._stream_tail_ws = delta[-1].isspace()
        if kind == "thinking" and duration is not None and duration != "":
            self._close_stream()

    def _close_stream(self) -> None:
        if self._open_kind is None:
            return
        self._open_kind = None
        self._stream_tail_ws = True
        self._write("\n", also_log=False)

    def _wrap(self, body: str) -> str:
        return f"## {_utc_stamp()}\n{body}\n"

    def _write_tokens(self, text: str) -> None:
        self._emit(self._tokens_fh, self.tokens_path, text, ensure_newline=True)

    def _write(
        self,
        text: str,
        *,
        also_log: bool = True,
        ensure_newline: bool = True,
    ) -> None:
        if also_log:
            summary = text.strip().splitlines()
            if summary:
                log.info("audit %s", " | ".join(summary[:2]))
        self._emit(self._fh, self.path, text, ensure_newline=ensure_newline)

    def _emit(
        self,
        fh: Optional[Any],
        path: Optional[Path],
        text: str,
        *,
        ensure_newline: bool,
    ) -> None:
        if fh is None:
            return
        try:
            if ensure_newline and not text.endswith("\n"):
                text = text + "\n"
            fh.write(text)
            fh.flush()
        except OSError as exc:
            log.warning("Agent audit write failed %s: %s", path, exc)
