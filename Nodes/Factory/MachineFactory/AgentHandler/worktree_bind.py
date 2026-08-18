"""Make Floor linked worktrees usable inside the Linux sandbox.

Host worktrees store Windows (or other host) paths in:
  <wt>/.git              -> gitdir: <host>/.bare/worktrees/<name>
  .bare/worktrees/<name>/gitdir -> <host>/<wt>/.git

``commondir`` is already relative (``../..``), but the gitdir pointers are not —
and they cannot be. The sandbox bind-mounts the worktree at /workspace and the
bare repo at /bare as two separate trees, so a path that is correct on the host
(absolute or relative-to-the-worktree) does not resolve inside the container.

Those two files must be rewritten for the container for the duration of the run,
then restored so the host — and MergeManager — can still see a git worktree.
The originals are written to a sidecar before any pointer changes, so restore
does not depend on in-memory state or on whatever an agent last wrote to .git.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
from pathlib import Path

log = logging.getLogger("agent_handler.worktree_bind")

_GITDIR_RE = re.compile(r"^gitdir:\s*(.+)\s*$", re.IGNORECASE | re.MULTILINE)
_SIDECAR_NAME = "wt-pointers.host"


def _posix(path: Path | str) -> str:
    return str(path).replace("\\", "/").rstrip("/")


def _is_container_pointer(text: str) -> bool:
    body = text.strip()
    match = _GITDIR_RE.search(body)
    target = match.group(1).strip() if match else body
    target = target.replace("\\", "/")
    return target.startswith("/bare/") or target.startswith("/workspace")


class WorktreeGitBind:
    """Rewrite linked-worktree gitdir pointers for /workspace + /bare mounts."""

    def __init__(
        self,
        workspace: Path | str,
        *,
        bare_mount: Path | str | None = None,
        ticket: str = "",
        host_wt: Path | str | None = None,
        host_bare: Path | str | None = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.bare = Path(
            bare_mount
            if bare_mount is not None
            else os.environ.get("BARE_MOUNT", "/bare")
        )
        self.ticket = ticket.strip()
        self.host_wt = _posix(
            host_wt if host_wt is not None else os.environ.get("HOST_WT", "")
        )
        self.host_bare = _posix(
            host_bare if host_bare is not None else os.environ.get("HOST_BARE", "")
        )
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

        host_workspace, host_backref = self._host_originals(git_path, gitdir_file)
        self._write_sidecar(host_workspace, host_backref)

        container_gitdir = f"gitdir: {admin.as_posix()}\n"
        container_backref = f"{(self.workspace / '.git').as_posix()}\n"

        self._saved = [(git_path, host_workspace), (gitdir_file, host_backref)]
        self._atomic_write(git_path, container_gitdir)
        self._atomic_write(gitdir_file, container_backref)

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
        """Put host gitdir pointers back. Safe to call if we never prepared.

        A previous sandbox can leave /bare and /workspace in the pointer files.
        Restore heals that even when this instance did not bind, so MergeManager
        is not stuck with a worktree git will not open on the host.
        """
        git_path = self.workspace / ".git"
        originals = list(self._saved)
        if not originals:
            originals = self._originals_for_restore(git_path)

        if not originals:
            self._clear_bind_state()
            return

        for path, content in reversed(originals):
            try:
                self._atomic_write(path, content)
            except OSError as exc:
                log.error("Failed to restore %s: %s", path, exc)

        self._clear_sidecar()
        self._clear_bind_state()
        log.info("Restored host worktree gitdir pointers")

    def _clear_bind_state(self) -> None:
        self._saved.clear()
        for key in ("GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE"):
            os.environ.pop(key, None)
        self._active = False

    def _originals_for_restore(
        self, git_path: Path
    ) -> list[tuple[Path, str]]:
        sidecar = self._read_sidecar()
        if sidecar is not None:
            admin = self._admin_dir_for_restore(git_path)
            return [
                (git_path, sidecar[0]),
                (admin / "gitdir", sidecar[1]),
            ]
        if git_path.is_file() and _is_container_pointer(
            git_path.read_text(encoding="utf-8")
        ):
            host_workspace, host_backref = self._reconstruct_host()
            admin = self._admin_dir_for_restore(git_path)
            return [(git_path, host_workspace), (admin / "gitdir", host_backref)]
        return []

    def _admin_dir_for_restore(self, git_path: Path) -> Path:
        if git_path.is_file():
            try:
                return self._resolve_admin_dir(git_path.read_text(encoding="utf-8"))
            except (FileNotFoundError, RuntimeError):
                pass
        name = self.ticket or Path(self.host_wt).name
        return self.bare / "worktrees" / name

    def _host_originals(self, git_path: Path, gitdir_file: Path) -> tuple[str, str]:
        current_git = git_path.read_text(encoding="utf-8")
        current_backref = gitdir_file.read_text(encoding="utf-8")
        sidecar = self._read_sidecar()
        if not _is_container_pointer(current_git) and not _is_container_pointer(
            current_backref
        ):
            return current_git, current_backref
        if sidecar is not None:
            return sidecar
        return self._reconstruct_host()

    def _reconstruct_host(self) -> tuple[str, str]:
        ticket = self.ticket or Path(self.host_wt).name
        if not self.host_wt or not self.host_bare or not ticket:
            raise RuntimeError(
                "Cannot reconstruct host gitdir pointers without HOST_WT, "
                "HOST_BARE, and a ticket/worktree name."
            )
        workspace_git = f"gitdir: {self.host_bare}/worktrees/{ticket}\n"
        admin_gitdir = f"{self.host_wt}/.git\n"
        log.warning(
            "Reconstructed host gitdir pointers from HOST_WT/HOST_BARE "
            "(ticket=%s)",
            ticket,
        )
        return workspace_git, admin_gitdir

    def _sidecar_path(self) -> Path:
        return self.workspace / ".machine_factory" / _SIDECAR_NAME

    def _write_sidecar(self, workspace_git: str, admin_gitdir: str) -> None:
        path = self._sidecar_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"workspace_git": workspace_git, "admin_gitdir": admin_gitdir}
            ),
            encoding="utf-8",
        )

    def _read_sidecar(self) -> tuple[str, str] | None:
        path = self._sidecar_path()
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        workspace_git = data.get("workspace_git")
        admin_gitdir = data.get("admin_gitdir")
        if not workspace_git or not admin_gitdir:
            return None
        return str(workspace_git), str(admin_gitdir)

    def _clear_sidecar(self) -> None:
        path = self._sidecar_path()
        try:
            path.unlink()
        except FileNotFoundError:
            return
        except OSError as exc:
            log.warning("Could not remove git pointer sidecar %s: %s", path, exc)

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

    def _atomic_write(self, path: Path, content: str) -> None:
        if path.is_file() and path.read_text(encoding="utf-8") == content:
            return
        tmp = path.with_name(f"{path.name}.machine-factory-tmp")
        tmp.write_text(content, encoding="utf-8")
        try:
            tmp.replace(path)
        except OSError:
            # Some hosts lock .git for in-place open(); unlink+rename often works.
            path.unlink()
            tmp.rename(path)
