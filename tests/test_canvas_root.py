"""Logs follow the running worktree, not the contracts install."""

from __future__ import annotations

from pathlib import Path

from megadesk_contracts.paths import (
    ENV_CANVAS_ROOT,
    ENV_LOGS_DIR,
    ENV_LOGS_ROOT,
    resolve_canvas_root,
    resolve_logs_root,
    resolve_worktree_root,
)


def test_canvas_root_prefers_env(monkeypatch, tmp_path: Path) -> None:
    canvas = tmp_path / "MegaDesk-Canvas"
    (canvas / "supervisor").mkdir(parents=True)
    (canvas / "main.py").write_text("# canvas\n", encoding="utf-8")
    monkeypatch.setenv(ENV_CANVAS_ROOT, str(canvas))
    assert resolve_canvas_root() == canvas.resolve()
    assert resolve_worktree_root() == tmp_path.resolve()


def test_canvas_root_uses_cwd_when_it_is_a_canvas(monkeypatch, tmp_path: Path) -> None:
    canvas = tmp_path / "MegaDesk-Canvas"
    (canvas / "supervisor").mkdir(parents=True)
    (canvas / "main.py").write_text("# canvas\n", encoding="utf-8")
    monkeypatch.delenv(ENV_CANVAS_ROOT, raising=False)
    monkeypatch.chdir(canvas)
    assert resolve_canvas_root() == canvas.resolve()


def test_logs_root_prefers_env(monkeypatch, tmp_path: Path) -> None:
    custom = tmp_path / "custom-logs"
    monkeypatch.setenv(ENV_LOGS_ROOT, str(custom))
    assert resolve_logs_root() == custom.resolve()


def test_logs_root_defaults_to_worktree_logs(monkeypatch, tmp_path: Path) -> None:
    canvas = tmp_path / "MegaDesk-Canvas"
    (canvas / "supervisor").mkdir(parents=True)
    (canvas / "main.py").write_text("# canvas\n", encoding="utf-8")
    monkeypatch.setenv(ENV_CANVAS_ROOT, str(canvas))
    monkeypatch.delenv(ENV_LOGS_ROOT, raising=False)
    monkeypatch.delenv(ENV_LOGS_DIR, raising=False)
    assert resolve_logs_root() == (tmp_path / "Logs").resolve()
