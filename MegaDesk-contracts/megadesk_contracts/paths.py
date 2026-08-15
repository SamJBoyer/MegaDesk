"""Resolve the MegaDesk-Canvas root of the *running* process.

Logs and Supervisor cwd must follow the canvas that launched this process, not
the worktree that last ``pip install -e``'d ``megadesk-contracts``. An editable
install from another worktree is the usual way logs leak across checkouts.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_CANVAS_ROOT = "MEGADESK_CANVAS_ROOT"


def _looks_like_canvas(path: Path) -> bool:
    return (path / "supervisor").is_dir() and (path / "main.py").is_file()


def resolve_canvas_root() -> Path:
    """Directory that owns ``logs/`` and ``python -m supervisor``.

    Order: ``MEGADESK_CANVAS_ROOT``, then cwd if it is a canvas, then the
    imported ``supervisor`` package, then the contracts-sibling fallback.
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


def resolve_logs_root() -> Path:
    return resolve_canvas_root() / "logs"
