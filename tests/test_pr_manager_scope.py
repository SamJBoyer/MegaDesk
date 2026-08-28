"""PRManager Scope: clone a PR head locally without launching editors.

These tests stay off the canvas. Git work is real; ``open_in_editor`` is
exercised with a fake Popen so the real ``code`` / ``cursor`` CLIs never start.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from megadesk_contracts.testing import GitFloor, git

pytestmark = pytest.mark.git


def publish_pull_ref(
    floor: GitFloor,
    number: int,
    *,
    relpath: str = "pr.txt",
    text: str | None = None,
) -> str:
    """Push a GitHub-style ``refs/pull/<n>/head`` onto the local origin."""
    ticket = floor.add_ticket(f"pr-{number}")
    body = f"changes from pr {number}\n" if text is None else text
    sha = floor.commit(ticket, relpath, body, f"pr {number}")
    git("push", str(floor.origin), f"HEAD:refs/pull/{number}/head", cwd=ticket)
    return sha


def test_pr_number_from_url() -> None:
    from pr_manager_app import pr_number_from_url

    assert pr_number_from_url("https://github.com/acme/widgets/pull/4") == 4
    assert pr_number_from_url("https://github.com/acme/widgets/pull/12/files") == 12
    assert pr_number_from_url("") is None
    assert pr_number_from_url("https://github.com/acme/widgets") is None


def test_pull_pr_checks_out_the_pull_ref(git_floor, tmp_path: Path) -> None:
    from pr_manager_app import pull_pr

    publish_pull_ref(git_floor, 4)
    dest = pull_pr(
        url=str(git_floor.origin),
        repo="widgets",
        pr_number=4,
        root=tmp_path / "Scope",
    )

    assert dest == tmp_path / "Scope" / "widgets" / "pr-4"
    assert (dest / ".git").exists()
    assert (dest / "pr.txt").read_text(encoding="utf-8") == "changes from pr 4\n"
    assert git("rev-parse", "--abbrev-ref", "HEAD", cwd=dest).stdout.strip() == "pr-4"


def test_pull_pr_again_resets_to_the_new_head(git_floor, tmp_path: Path) -> None:
    from pr_manager_app import pull_pr

    publish_pull_ref(git_floor, 4, text="first\n")
    root = tmp_path / "Scope"
    dest = pull_pr(url=str(git_floor.origin), repo="widgets", pr_number=4, root=root)
    assert (dest / "pr.txt").read_text(encoding="utf-8") == "first\n"

    ticket = git_floor.ticket_dir("pr-4")
    git_floor.commit(ticket, "pr.txt", "second\n", "pr 4 follow-up")
    git("push", str(git_floor.origin), f"HEAD:refs/pull/4/head", cwd=ticket)

    again = pull_pr(url=str(git_floor.origin), repo="widgets", pr_number=4, root=root)
    assert again == dest
    assert (dest / "pr.txt").read_text(encoding="utf-8") == "second\n"


def test_open_in_editor_requires_a_directory(tmp_path: Path) -> None:
    from pr_manager_app import open_in_editor

    ok, msg = open_in_editor("vscode", tmp_path / "missing")
    assert not ok
    assert "does not exist" in msg


def test_open_in_editor_launches_the_cli(monkeypatch, tmp_path: Path) -> None:
    import pr_manager_app

    calls: list[list[str]] = []

    class _Proc:
        pass

    def fake_popen(cmd, **_kwargs):
        calls.append(list(cmd))
        return _Proc()

    monkeypatch.setattr(pr_manager_app.subprocess, "Popen", fake_popen)

    ok, msg = pr_manager_app.open_in_editor("vscode", tmp_path)
    assert ok
    assert calls == [["code", str(tmp_path)]]
    assert "vscode" in msg

    ok, msg = pr_manager_app.open_in_editor("cursor", tmp_path)
    assert ok
    assert calls[-1] == ["cursor", str(tmp_path)]


def test_open_in_editor_reports_a_missing_cli(monkeypatch, tmp_path: Path) -> None:
    import pr_manager_app

    def fake_popen(cmd, **_kwargs):
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(pr_manager_app.subprocess, "Popen", fake_popen)
    ok, msg = pr_manager_app.open_in_editor("vscode", tmp_path)
    assert not ok
    assert "not found" in msg
