"""Linked-worktree gitdir rewrite for the MachineFactory sandbox.

No Docker. Pointer files are written by hand; the bind has to put container
paths in them and host paths back, including when a previous run left
``gitdir: /bare/...`` behind.
"""

from __future__ import annotations

from pathlib import Path

from AgentHandler.worktree_bind import WorktreeGitBind

HOST_WT = "C:/host/Floor/widgets/wt/tickets/add-widget-tests"
HOST_BARE = "C:/host/Floor/widgets/.bare"
HOST_GIT = f"gitdir: {HOST_BARE}/worktrees/add-widget-tests\n"
HOST_BACKREF = f"{HOST_WT}/.git\n"
STRANDED_GIT = "gitdir: /bare/worktrees/add-widget-tests\n"
STRANDED_BACKREF = "/workspace/.git\n"


def _linked_tree(root: Path) -> tuple[Path, Path]:
    workspace = root / "workspace"
    bare = root / "bare"
    admin = bare / "worktrees" / "add-widget-tests"
    admin.mkdir(parents=True)
    workspace.mkdir()
    (admin / "HEAD").write_text("ref: refs/heads/ticket/add-widget-tests\n", encoding="utf-8")
    (workspace / ".git").write_text(HOST_GIT, encoding="utf-8")
    (admin / "gitdir").write_text(HOST_BACKREF, encoding="utf-8")
    return workspace, bare


def _container_pointers(workspace: Path, bare: Path) -> tuple[str, str]:
    admin = (bare / "worktrees" / "add-widget-tests").as_posix()
    return (
        f"gitdir: {admin}\n",
        f"{(workspace / '.git').as_posix()}\n",
    )


def _bind(workspace: Path, bare: Path) -> WorktreeGitBind:
    return WorktreeGitBind(
        workspace,
        bare_mount=bare,
        ticket="add-widget-tests",
        host_wt=HOST_WT,
        host_bare=HOST_BARE,
    )


def test_prepare_rewrites_pointers_for_the_sandbox(tmp_path: Path) -> None:
    workspace, bare = _linked_tree(tmp_path)
    binder = _bind(workspace, bare)
    binder.prepare()
    container_git, container_backref = _container_pointers(workspace, bare)

    assert (workspace / ".git").read_text(encoding="utf-8") == container_git
    assert (bare / "worktrees" / "add-widget-tests" / "gitdir").read_text(
        encoding="utf-8"
    ) == container_backref
    sidecar = workspace / ".machine_factory" / "wt-pointers.host"
    assert sidecar.is_file()


def test_restore_puts_host_pointers_back(tmp_path: Path) -> None:
    workspace, bare = _linked_tree(tmp_path)
    binder = _bind(workspace, bare)
    binder.prepare()
    binder.restore()

    assert (workspace / ".git").read_text(encoding="utf-8") == HOST_GIT
    assert (bare / "worktrees" / "add-widget-tests" / "gitdir").read_text(
        encoding="utf-8"
    ) == HOST_BACKREF
    assert not (workspace / ".machine_factory" / "wt-pointers.host").exists()


def test_restore_heals_a_worktree_left_on_container_paths(tmp_path: Path) -> None:
    workspace, bare = _linked_tree(tmp_path)
    (workspace / ".git").write_text(STRANDED_GIT, encoding="utf-8")
    (bare / "worktrees" / "add-widget-tests" / "gitdir").write_text(
        STRANDED_BACKREF, encoding="utf-8"
    )

    _bind(workspace, bare).restore()

    assert (workspace / ".git").read_text(encoding="utf-8") == HOST_GIT
    assert (bare / "worktrees" / "add-widget-tests" / "gitdir").read_text(
        encoding="utf-8"
    ) == HOST_BACKREF


def test_prepare_then_restore_heals_stranded_container_paths(tmp_path: Path) -> None:
    workspace, bare = _linked_tree(tmp_path)
    (workspace / ".git").write_text(STRANDED_GIT, encoding="utf-8")
    (bare / "worktrees" / "add-widget-tests" / "gitdir").write_text(
        STRANDED_BACKREF, encoding="utf-8"
    )

    binder = _bind(workspace, bare)
    binder.prepare()
    container_git, container_backref = _container_pointers(workspace, bare)
    assert (workspace / ".git").read_text(encoding="utf-8") == container_git
    assert (bare / "worktrees" / "add-widget-tests" / "gitdir").read_text(
        encoding="utf-8"
    ) == container_backref
    binder.restore()
    assert (workspace / ".git").read_text(encoding="utf-8") == HOST_GIT
    assert (bare / "worktrees" / "add-widget-tests" / "gitdir").read_text(
        encoding="utf-8"
    ) == HOST_BACKREF
