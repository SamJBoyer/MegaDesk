"""Path helpers for the GBD commander."""

from __future__ import annotations

from pathlib import Path

# Supervisor package root (Nodes/Supervisor); ~NODES/ expands to Nodes/.
REPO_ROOT = Path(__file__).resolve().parent.parent
NODES_ROOT = REPO_ROOT.parent
NODES_PREFIX = "~NODES/"


def resolve_directory(directory: str) -> Path:
    """Resolve a manifest directory, expanding ~NODES/ to the Nodes/ folder."""
    raw = directory.strip()
    if raw.startswith(NODES_PREFIX):
        rel = raw[len(NODES_PREFIX) :].lstrip("/\\")
        return (NODES_ROOT / rel).resolve()
    path = Path(raw)
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path.resolve()


def resolve_manifest_path(path_str: str) -> Path:
    """Resolve a manifest path relative to the Supervisor package when not absolute."""
    path = Path(path_str.strip().strip('"').strip("'"))
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path.resolve()
