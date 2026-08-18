"""Per-run agent audit transcripts: format, path, and streamed SDK progress."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from megadesk_contracts.agent_audit import (
    CONTAINER_AUDIT_PATH,
    ENV_AGENT_AUDIT_PATH,
    AgentAuditLog,
    agent_audit_bind_args,
    ensure_agent_audit_file,
    format_sdk_message,
    resolve_audit_path,
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


def test_bind_args_mount_a_single_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(ENV_LOGS_ROOT, str(tmp_path / "Logs"))
    monkeypatch.delenv(ENV_LOGS_DIR, raising=False)
    begin_log_session()
    guid = "guid-for-bind"
    args = agent_audit_bind_args(guid)
    assert "-v" in args
    assert "-e" in args
    volume = args[args.index("-v") + 1]
    env = args[args.index("-e") + 1]
    assert volume.endswith(f":{CONTAINER_AUDIT_PATH}")
    assert "agent-guid-for-bind.md" in volume
    assert env == f"{ENV_AGENT_AUDIT_PATH}={CONTAINER_AUDIT_PATH}"


def test_resolve_path_prefers_explicit_mount(tmp_path: Path, monkeypatch) -> None:
    mounted = tmp_path / "mounted.md"
    monkeypatch.setenv(ENV_AGENT_AUDIT_PATH, str(mounted))
    assert resolve_audit_path("any") == mounted


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
