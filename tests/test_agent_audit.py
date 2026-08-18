"""Per-run agent audit transcripts: format, path, and streamed SDK progress."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from megadesk_contracts.agent_audit import (
    CONTAINER_AUDIT_PATH,
    CONTAINER_AUDIT_TOKENS_PATH,
    ENV_AGENT_AUDIT_PATH,
    ENV_AGENT_AUDIT_TOKENS_PATH,
    AgentAuditLog,
    agent_audit_bind_args,
    ensure_agent_audit_file,
    format_sdk_message,
    resolve_audit_path,
    resolve_tokens_path,
    tokens_path_beside,
)
from megadesk_contracts.log_session import begin_log_session, session_log_path
from megadesk_contracts.paths import ENV_LOGS_DIR, ENV_LOGS_ROOT


def test_format_skips_usage_and_empty_assistant() -> None:
    assert format_sdk_message(SimpleNamespace(type="usage", usage={"in": 1})) is None
    assert format_sdk_message({"type": "request", "request_id": "r1"}) is None
    empty = SimpleNamespace(
        type="assistant",
        message=SimpleNamespace(content=()),
    )
    assert format_sdk_message(empty) is None


def test_format_tool_call_and_thinking() -> None:
    tool = SimpleNamespace(
        type="tool_call",
        name="Read",
        status="running",
        call_id="c1",
        args={"path": "src/foo.py"},
        result=None,
    )
    text = format_sdk_message(tool)
    assert text is not None
    assert "**tool_call** `Read` running" in text
    assert "src/foo.py" in text

    thinking = {"type": "thinking", "text": "considering the layout", "thinking_duration_ms": 12}
    thought = format_sdk_message(thinking)
    assert thought is not None
    assert "**thinking** (12ms)" in thought
    assert "considering the layout" in thought


def test_format_assistant_text_blocks() -> None:
    message = SimpleNamespace(
        type="assistant",
        message=SimpleNamespace(
            content=[SimpleNamespace(type="text", text="I'll add a test.")]
        ),
    )
    text = format_sdk_message(message)
    assert text is not None
    assert "**assistant**" in text
    assert "I'll add a test." in text


def test_ensure_audit_file_lives_in_the_session_folder(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv(ENV_LOGS_ROOT, str(tmp_path / "Logs"))
    monkeypatch.delenv(ENV_LOGS_DIR, raising=False)
    session = begin_log_session()
    guid = "deadbeef0123456789"
    path = ensure_agent_audit_file(guid)
    assert path.parent == session
    assert path.name == f"agent-{guid}.md"
    assert path.is_file()
    assert path == session_log_path(f"agent-{guid}")
    tokens = tokens_path_beside(path)
    assert tokens.is_file()
    assert tokens.name == f"agent-{guid}.tokens.md"


def test_bind_args_mount_pretty_and_token_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(ENV_LOGS_ROOT, str(tmp_path / "Logs"))
    monkeypatch.delenv(ENV_LOGS_DIR, raising=False)
    begin_log_session()
    guid = "guid-for-bind"
    args = agent_audit_bind_args(guid)
    volumes = [args[i + 1] for i, flag in enumerate(args) if flag == "-v"]
    envs = [args[i + 1] for i, flag in enumerate(args) if flag == "-e"]
    assert any(v.endswith(f":{CONTAINER_AUDIT_PATH}") for v in volumes)
    assert any(v.endswith(f":{CONTAINER_AUDIT_TOKENS_PATH}") for v in volumes)
    assert any("agent-guid-for-bind.md" in v for v in volumes)
    assert any("agent-guid-for-bind.tokens.md" in v for v in volumes)
    assert f"{ENV_AGENT_AUDIT_PATH}={CONTAINER_AUDIT_PATH}" in envs
    assert f"{ENV_AGENT_AUDIT_TOKENS_PATH}={CONTAINER_AUDIT_TOKENS_PATH}" in envs


def test_resolve_path_prefers_explicit_mount(tmp_path: Path, monkeypatch) -> None:
    mounted = tmp_path / "mounted.md"
    tokens = tmp_path / "mounted.tokens.md"
    monkeypatch.setenv(ENV_AGENT_AUDIT_PATH, str(mounted))
    monkeypatch.setenv(ENV_AGENT_AUDIT_TOKENS_PATH, str(tokens))
    assert resolve_audit_path("any") == mounted
    assert resolve_tokens_path("any", mounted) == tokens


def _assistant(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        type="assistant",
        message=SimpleNamespace(content=[SimpleNamespace(type="text", text=text)]),
    )


def test_audit_coalesces_streamed_tokens(tmp_path: Path) -> None:
    path = tmp_path / "agent-run.md"
    audit = AgentAuditLog("coalesce", path=path)
    try:
        audit.sdk_message({"type": "thinking", "text": "Preparing the smoke"})
        audit.sdk_message({"type": "thinking", "text": "test worktree."})
        audit.sdk_message({"type": "thinking", "thinking_duration_ms": 428})
        audit.sdk_message(_assistant("I'll"))
        audit.sdk_message(_assistant(" inspect"))
        audit.sdk_message(_assistant(" the"))
        audit.sdk_message(_assistant(" repo"))
        audit.sdk_message({"type": "usage", "usage": {"in": 1}})
        audit.sdk_message(_assistant(" layout"))
        audit.sdk_message(
            SimpleNamespace(
                type="tool_call",
                name="Read",
                status="running",
                call_id="c1",
                args={"path": "README.md"},
                result=None,
            )
        )
        audit.sdk_message({"type": "thinking", "text": "now looking"})
        audit.sdk_message({"type": "thinking", "text": " at files"})
    finally:
        audit.close()
    body = path.read_text(encoding="utf-8")
    assert body.count("**thinking**") == 2
    assert "Preparing the smoke test worktree." in body
    assert body.count("**assistant**") == 1
    assert "I'll inspect the repo layout" in body
    assert body.count("## ") == 4
    assert "**thinking** (428ms)" not in body
    assert "**tool_call** `Read` running" in body
    assert "now looking at files" in body
    raw = path.with_suffix(".tokens.md").read_text(encoding="utf-8")
    assert raw.count("**assistant**") == 5
    assert raw.count("**thinking**") >= 4
    assert "**thinking** (428ms)" in raw
    assert "I'll inspect the repo layout" not in raw
    assert raw.count("## ") > body.count("## ")


def test_audit_keeps_assistant_subword_tokens(tmp_path: Path) -> None:
    path = tmp_path / "agent-run.md"
    audit = AgentAuditLog("tokens", path=path)
    try:
        audit.sdk_message(_assistant("Increment"))
        audit.sdk_message(_assistant("ed"))
        audit.sdk_message(_assistant(" `"))
        audit.sdk_message(_assistant("counter"))
        audit.sdk_message(_assistant(".txt"))
        audit.sdk_message(_assistant("`"))
    finally:
        audit.close()
    body = path.read_text(encoding="utf-8")
    assert body.count("**assistant**") == 1
    assert "Incremented `counter.txt`" in body
    raw = path.with_suffix(".tokens.md").read_text(encoding="utf-8")
    assert raw.count("**assistant**") == 6
    assert "Incremented `counter.txt`" not in raw


def test_audit_log_flushes_events_and_sdk_messages(tmp_path: Path) -> None:
    path = tmp_path / "agent-run.md"
    audit = AgentAuditLog("abc123", path=path, repo="Helmsman", ticket="1", model="auto")
    try:
        audit.header()
        audit.event("run", "run_id=r1")
        audit.sdk_message(
            SimpleNamespace(
                type="tool_call",
                name="Shell",
                status="completed",
                call_id="c2",
                args={"command": "pytest"},
                result={"exit_code": 0},
            )
        )
    finally:
        audit.close()
    body = path.read_text(encoding="utf-8")
    assert "Agent audit `abc123`" in body
    assert "repo: Helmsman" in body
    assert "**run** run_id=r1" in body
    assert "**tool_call** `Shell` completed" in body
    assert "pytest" in body


def test_run_agent_streams_progress_into_the_audit_file(
    tmp_path: Path, monkeypatch
) -> None:
    from AgentHandler.handler import run_agent

    class FakeRun:
        id = "run-9"

        def messages(self):
            yield SimpleNamespace(
                type="assistant",
                message=SimpleNamespace(
                    content=[SimpleNamespace(type="text", text="editing the file")]
                ),
            )
            yield SimpleNamespace(
                type="tool_call",
                name="Write",
                status="running",
                call_id="w1",
                args={"path": "note.txt"},
                result=None,
            )

        def wait(self):
            return SimpleNamespace(status="finished", result="ok")

        def text(self):
            return "ok"

    class FakeAgent:
        agent_id = "ag-9"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def send(self, instruction):
            assert "Create it" in instruction
            return FakeRun()

        @classmethod
        def create(cls, **kwargs):
            return cls()

    monkeypatch.setattr("AgentHandler.handler.Agent", FakeAgent)
    path = tmp_path / "streamed.md"
    audit = AgentAuditLog("stream-guid", path=path, repo="demo", ticket="t")
    try:
        outcome = run_agent(
            "Create it",
            cwd=str(tmp_path),
            api_key="fake",
            model="auto",
            audit=audit,
        )
    finally:
        audit.close()
    assert outcome["status"] == "finished"
    assert outcome["agent_id"] == "ag-9"
    body = path.read_text(encoding="utf-8")
    assert "**created** agent_id=ag-9" in body
    assert "**run** run_id=run-9" in body
    assert "editing the file" in body
    assert "**tool_call** `Write` running" in body
    assert "**agent-finished**" in body
