"""Resolve the MegaDesk-Canvas root and worktree ``Logs/`` of the *running* process.

Supervisor cwd follows the canvas that launched this process, not the worktree
that last ``pip install -e``'d ``megadesk-contracts``. Session transcripts live
at the worktree ``Logs/`` (``resolve_logs_root``), never another checkout's
install path.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_CANVAS_ROOT = "MEGADESK_CANVAS_ROOT"
ENV_LOGS_ROOT = "MEGADESK_LOGS_ROOT"
ENV_LOGS_DIR = "MEGADESK_LOGS_DIR"


def _looks_like_canvas(path: Path) -> bool:
    return (path / "supervisor").is_dir() and (path / "main.py").is_file()


def resolve_canvas_root() -> Path:
    """Directory that owns ``python -m supervisor``.

    Order: ``MEGADESK_CANVAS_ROOT``, then cwd if it is a canvas, then the
    imported ``supervisor`` package, then the contracts-sibling fallback.
    Session transcripts live under the worktree ``Logs/`` (see
    ``resolve_logs_root``), not inside this directory.
    """
    env = (os.environ.get(ENV_CANVAS_ROOT) or "").strip()
    if env:
        return Path(env).expanduser().resolve()

    cwd = Path.cwd()
    if _looks_like_canvas(cwd):
        return cwd.resolve()
    if cwd.name == "MegaDesk-Canvas" and (cwd / "supervisor").is_dir():
        return cwd.resolve()

    try:
        import supervisor

        imported = Path(supervisor.__file__).resolve().parent.parent
        if _looks_like_canvas(imported):
            return imported
    except Exception:
        pass

    sibling = Path(__file__).resolve().parents[2] / "MegaDesk-Canvas"
    return sibling.resolve()


def resolve_worktree_root() -> Path:
    """Worktree that owns ``Logs/``, ``Nodes/``, and ``MegaDesk-Canvas/``."""
    return resolve_canvas_root().parent


def resolve_logs_root() -> Path:
    """``Logs/`` home (CURRENT + session folders), not a live session directory.

    Order: ``MEGADESK_LOGS_ROOT``, then ``<worktree>/Logs``.
    """
    env = (os.environ.get(ENV_LOGS_ROOT) or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return (resolve_worktree_root() / "Logs").resolve()
