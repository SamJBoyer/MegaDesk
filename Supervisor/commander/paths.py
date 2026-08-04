"""Path helpers for the GBD commander."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
NODES_ROOT = REPO_ROOT
NODES_PREFIX = "~NODES/"


def resolve_directory(directory: str) -> Path:
    """Resolve a manifest directory, expanding ~NODES/ to the repo nodes root."""
    raw = directory.strip()
    if raw.startswith(NODES_PREFIX):
        rel = raw[len(NODES_PREFIX) :].lstrip("/\\")
        return (NODES_ROOT / rel).resolve()
    path = Path(raw)
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path.resolve()


def resolve_manifest_path(path_str: str) -> Path:
    """Resolve a manifest path relative to the repo root when not absolute."""
    path = Path(path_str.strip().strip('"').strip("'"))
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path.resolve()
