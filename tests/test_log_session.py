"""Supervisor log sessions: write in place, one file per node, CURRENT pointer."""

from __future__ import annotations

import json
import os
from pathlib import Path

from megadesk_contracts.log_session import (
    CURRENT_FILENAME,
    attach_log_session,
    begin_log_session,
    read_current_pointer,
    session_log_path,
)
from megadesk_contracts.paths import ENV_LOGS_DIR, ENV_LOGS_ROOT


def test_begin_log_session_writes_current_and_folder(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "Logs"
    monkeypatch.setenv(ENV_LOGS_ROOT, str(home))
    monkeypatch.delenv(ENV_LOGS_DIR, raising=False)
    session = begin_log_session(supervisor_pid=4242)
    assert session.is_dir()
    assert session.parent == home.resolve()
    assert ":" not in session.name
    pointer = read_current_pointer()
    assert pointer is not None
    assert pointer["session"] == session.name
    assert pointer["supervisor_pid"] == 4242
    raw = json.loads((home / CURRENT_FILENAME).read_text(encoding="utf-8"))
    assert raw["session"] == session.name
    assert os.environ[ENV_LOGS_DIR] == str(session)


def test_attach_reuses_current_and_does_not_move_files(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "Logs"
    monkeypatch.setenv(ENV_LOGS_ROOT, str(home))
    monkeypatch.delenv(ENV_LOGS_DIR, raising=False)
    first = begin_log_session()
    (first / "mission_control.md").write_text("kept\n", encoding="utf-8")
    monkeypatch.delenv(ENV_LOGS_DIR, raising=False)
    attached = attach_log_session()
    assert attached == first
    assert (first / "mission_control.md").read_text(encoding="utf-8") == "kept\n"
    assert list(home.iterdir())  # original folder still there


def test_begin_creates_sibling_and_leaves_old_session(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "Logs"
    monkeypatch.setenv(ENV_LOGS_ROOT, str(home))
    monkeypatch.delenv(ENV_LOGS_DIR, raising=False)
    first = begin_log_session()
    (first / "voice_deck.md").write_text("old\n", encoding="utf-8")
    monkeypatch.delenv(ENV_LOGS_DIR, raising=False)
    second = begin_log_session()
    assert second != first
    assert first.is_dir()
    assert (first / "voice_deck.md").read_text(encoding="utf-8") == "old\n"
    assert read_current_pointer()["session"] == second.name


def test_session_log_path_is_one_md_per_node(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "Logs"
    monkeypatch.setenv(ENV_LOGS_ROOT, str(home))
    monkeypatch.delenv(ENV_LOGS_DIR, raising=False)
    session = begin_log_session()
    path = session_log_path("mission_control")
    assert path.parent == session
    assert path.name == "mission_control.md"
    other = session_log_path("mission_control")
    assert other == path


def test_instance_log_path_matches_session_file(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "Logs"
    monkeypatch.setenv(ENV_LOGS_ROOT, str(home))
    monkeypatch.delenv(ENV_LOGS_DIR, raising=False)
    begin_log_session()
    from supervisor.process_registry import instance_log_path

    path = instance_log_path("code_scope", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    assert path == session_log_path("code_scope")
    assert path.name == "code_scope.md"
