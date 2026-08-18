"""Supervisor-owned log sessions: write in place, never relocate live files.

A session is one Supervisor generation (not a MegaDesk-Canvas open). Layout::

    Logs/
      CURRENT                 # JSON pointer at the live session folder
      README.md
      2026-08-17T20-55-03Z/
        supervisor.md
        canvas.md
        machine_factory.md

``CURRENT`` is a pointer only. Timestamp folders are the archive: a new Supervisor
process creates a sibling folder and points ``CURRENT`` at it. Files are born in
their session folder and are not moved.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from megadesk_contracts.paths import ENV_LOGS_DIR, ENV_LOGS_ROOT, resolve_logs_root

CURRENT_FILENAME = "CURRENT"
LOG_SUFFIX = ".md"
_SESSION_ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}(?:-\d+)?Z$")
_UNSAFE_STEM = re.compile(r"[^\w.-]+", flags=re.ASCII)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_session_id() -> str:
    """Windows-safe UTC stamp (colons are not allowed in NTFS names)."""
    return _utc_now().strftime("%Y-%m-%dT%H-%M-%SZ")


def current_pointer_path() -> Path:
    return resolve_logs_root() / CURRENT_FILENAME


def safe_log_stem(source: str) -> str:
    raw = (source or "unknown").strip() or "unknown"
    cleaned = _UNSAFE_STEM.sub("_", raw).strip("._")
    return cleaned or "unknown"


def session_log_path(source: str) -> Path:
    """``Logs/{session}/{source}.md`` for the attached session."""
    return (resolve_session_log_dir() / f"{safe_log_stem(source)}{LOG_SUFFIX}").resolve()


def _write_current(payload: Mapping[str, Any]) -> None:
    root = resolve_logs_root()
    root.mkdir(parents=True, exist_ok=True)
    path = current_pointer_path()
    tmp = root / f"{CURRENT_FILENAME}.tmp"
    tmp.write_text(json.dumps(dict(payload), indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def read_current_pointer() -> Optional[dict[str, Any]]:
    """Parse ``Logs/CURRENT``. Returns None if missing or not a session id."""
    path = current_pointer_path()
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text:
        return None
    data: dict[str, Any]
    if text.startswith("{"):
        try:
            loaded = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(loaded, dict):
            return None
        data = loaded
    else:
        data = {"session": text.splitlines()[0].strip()}
    session = str(data.get("session") or "").strip()
    if not session or not _SESSION_ID_RE.match(session):
        return None
    data["session"] = session
    return data


def update_current_session(**fields: Any) -> Optional[dict[str, Any]]:
    """Merge fields into ``CURRENT``. No-op if there is no valid pointer."""
    data = read_current_pointer()
    if data is None:
        return None
    data.update(fields)
    _write_current(data)
    return data


def resolve_session_log_dir() -> Path:
    """Directory where the live session's ``*.md`` files are written.

    Order: ``MEGADESK_LOGS_DIR``, then ``Logs/CURRENT``. Raises if neither is set.
    """
    env = (os.environ.get(ENV_LOGS_DIR) or "").strip()
    if env:
        path = Path(env).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path
    pointer = read_current_pointer()
    if pointer is None:
        raise RuntimeError(
            "No log session is attached; call begin_log_session() or attach_log_session()"
        )
    path = (resolve_logs_root() / str(pointer["session"])).resolve()
    root = resolve_logs_root().resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(f"CURRENT session path escapes Logs/: {path}") from exc
    path.mkdir(parents=True, exist_ok=True)
    os.environ[ENV_LOGS_DIR] = str(path)
    return path


def begin_log_session(*, supervisor_pid: int | None = None) -> Path:
    """Start a new Supervisor generation: new folder, point CURRENT at it.

    Does not rename or move any existing session folder.
    """
    logs_root = resolve_logs_root()
    logs_root.mkdir(parents=True, exist_ok=True)
    session_id = new_session_id()
    session_dir = logs_root / session_id
    if session_dir.exists():
        session_id = _utc_now().strftime("%Y-%m-%dT%H-%M-%S-%fZ")
        session_dir = logs_root / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "session": session_id,
        "started_at": _utc_now().isoformat(),
    }
    if supervisor_pid is not None:
        payload["supervisor_pid"] = int(supervisor_pid)
    _write_current(payload)
    os.environ[ENV_LOGS_DIR] = str(session_dir.resolve())
    if not (os.environ.get(ENV_LOGS_ROOT) or "").strip():
        os.environ[ENV_LOGS_ROOT] = str(logs_root.resolve())
    return session_dir.resolve()


def attach_log_session() -> Path:
    """Reuse CURRENT / ``MEGADESK_LOGS_DIR``, or begin a session if none exists."""
    env = (os.environ.get(ENV_LOGS_DIR) or "").strip()
    if env:
        return resolve_session_log_dir()
    if read_current_pointer() is not None:
        return resolve_session_log_dir()
    return begin_log_session()
