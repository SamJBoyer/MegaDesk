"""Make Floor linked worktrees usable inside the Linux sandbox.

Host worktrees store Windows (or other host) absolute paths in:
  <wt>/.git              -> gitdir: <host>/.bare/worktrees/<name>
  .bare/worktrees/<name>/gitdir -> <host>/<wt>/.git

Only the worktree and .bare are mounted (/workspace and /bare), so those
pointers must be rewritten for the container for the duration of the run,
then restored so the host keeps working.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from pathlib import Path

log = logging.getLogger("agent_handler.worktree_bind")

_GITDIR_RE = re.compile(r"^gitdir:\s*(.+)\s*$", re.IGNORECASE | re.MULTILINE)


class WorktreeGitBind:
    """Rewrite linked-worktree gitdir pointers for /workspace + /bare mounts."""

    def __init__(
        self,
        workspace: Path | str,
        *,
        bare_mount: Path | str | None = None,
        ticket: str = "",
    ) -> None:
        self.workspace = Path(workspace)
        self.bare = Path(
            bare_mount
            if bare_mount is not None
            else os.environ.get("BARE_MOUNT", "/bare")
        )
        self.ticket = ticket.strip()
        self._saved: list[tuple[Path, str]] = []
        self._active = False

    def __enter__(self) -> WorktreeGitBind:
        self.prepare()
        return self

    def __exit__(self, *exc: object) -> None:
        self.restore()

    def prepare(self) -> None:
        if self._active:
            return
        if not self.bare.is_dir():
            raise FileNotFoundError(
                f"Bare mount missing at {self.bare}. "
                "MachineFactoryManager must mount Floor/<repo>/.bare at BARE_MOUNT."
            )

        git_path = self.workspace / ".git"
        self._recover_from_broken_init(git_path)

        if not git_path.is_file():
            raise RuntimeError(
                f"{git_path} is not a linked-worktree pointer file. "
                "Cannot bind sandbox git to /bare."
            )

        original = git_path.read_text(encoding="utf-8")
        admin = self._resolve_admin_dir(original)
        gitdir_file = admin / "gitdir"
        if not gitdir_file.is_file():
            raise FileNotFoundError(f"Missing worktree gitdir file: {gitdir_file}")

        container_gitdir = f"gitdir: {admin.as_posix()}\n"
        container_backref = f"{(self.workspace / '.git').as_posix()}\n"

        self._save_and_write(git_path, container_gitdir)
        self._save_and_write(gitdir_file, container_backref)

        # Belt-and-suspenders for tools that honor these without reading .git.
        os.environ["GIT_DIR"] = str(admin)
        os.environ["GIT_COMMON_DIR"] = str(self.bare)
        os.environ["GIT_WORK_TREE"] = str(self.workspace)

        self._active = True
        log.info(
            "Bound worktree git: workspace=%s admin=%s bare=%s",
            self.workspace,
            admin,
            self.bare,
        )

    def restore(self) -> None:
        if not self._active and not self._saved:
            return
        for path, content in reversed(self._saved):
            try:
                path.write_text(content, encoding="utf-8")
            except OSError as exc:
                log.error("Failed to restore %s: %s", path, exc)
        self._saved.clear()
        for key in ("GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE"):
            os.environ.pop(key, None)
        self._active = False
        log.info("Restored host worktree gitdir pointers")

    def _recover_from_broken_init(self, git_path: Path) -> None:
        """Undo agent ``git init`` that replaced the worktree pointer."""
        broken = self.workspace / ".git.broken-worktree-pointer"
        if not (git_path.is_dir() and broken.is_file()):
            return
        log.warning(
            "Recovering linked worktree: moving re-inited %s aside, restoring %s",
            git_path,
            broken,
        )
        orphan = self.workspace / ".git.orphan-init"
        if orphan.exists():
            shutil.rmtree(orphan, ignore_errors=True)
            if orphan.exists():
                # Last resort on locked Windows trees: unique aside name.
                orphan = self.workspace / f".git.orphan-init-{os.getpid()}"
        try:
            git_path.rename(orphan)
        except OSError:
            shutil.rmtree(git_path)
        broken.replace(git_path)
        # Best-effort cleanup; ignore locks (common on Windows hosts).
        shutil.rmtree(orphan, ignore_errors=True)

    def _resolve_admin_dir(self, git_pointer_text: str) -> Path:
        match = _GITDIR_RE.search(git_pointer_text)
        if not match:
            raise RuntimeError(
                f"Could not parse gitdir pointer in {self.workspace / '.git'}"
            )
        host_admin = Path(match.group(1).strip())
        name = host_admin.name
        candidates = [name]
        if self.ticket and self.ticket not in candidates:
            candidates.append(self.ticket)
        for candidate in candidates:
            admin = self.bare / "worktrees" / candidate
            if admin.is_dir() and (admin / "HEAD").is_file():
                return admin
        raise FileNotFoundError(
            f"No matching worktree admin dir under {self.bare / 'worktrees'} "
            f"(tried: {', '.join(candidates)})"
        )

    def _save_and_write(self, path: Path, content: str) -> None:
        previous = path.read_text(encoding="utf-8")
        if previous == content:
            return
        self._saved.append((path, previous))
        tmp = path.with_name(f"{path.name}.machine-factory-tmp")
        tmp.write_text(content, encoding="utf-8")
        try:
            tmp.replace(path)
        except OSError:
            # Some hosts lock .git for in-place open(); unlink+rename often works.
            path.unlink()
            tmp.rename(path)
