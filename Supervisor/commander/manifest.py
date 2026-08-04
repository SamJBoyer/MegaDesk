"""Manifest parse and validation (EE-11 / IP-1–5)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from commander.paths import resolve_directory, resolve_manifest_path


@dataclass(frozen=True)
class NodeSpec:
    nickname: str
    directory: Path
    target: str
    parameters: dict[str, str]


@dataclass(frozen=True)
class Manifest:
    path: Path
    nodes: list[NodeSpec]


class ManifestError(ValueError):
    """Raised when a manifest fails validation."""


def _as_str_map(raw: Any, nickname: str) -> dict[str, str]:
    if raw is None:
        raise ManifestError(f"Node '{nickname}' missing parameters map")
    if not isinstance(raw, dict):
        raise ManifestError(f"Node '{nickname}' parameters must be a map")
    if not raw:
        raise ManifestError(f"Node '{nickname}' parameters map is empty")
    out: dict[str, str] = {}
    for key, value in raw.items():
        out[str(key)] = "" if value is None else str(value)
    return out


def _parse_node_entry(entry: Any) -> NodeSpec:
    if not isinstance(entry, dict) or len(entry) != 1:
        raise ManifestError("Each nodes: item must be a single-key map of nickname → fields")
    nickname, body = next(iter(entry.items()))
    nickname = str(nickname).strip()
    if not nickname:
        raise ManifestError("Node nickname/ID must be non-empty")
    if not isinstance(body, dict):
        raise ManifestError(f"Node '{nickname}' body must be a map")

    directory_raw = body.get("directory")
    target = body.get("target")
    if not directory_raw or not isinstance(directory_raw, str):
        raise ManifestError(f"Node '{nickname}' missing directory")
    if not target or not isinstance(target, str):
        raise ManifestError(f"Node '{nickname}' missing target")

    directory = resolve_directory(directory_raw)
    if not directory.is_dir():
        raise ManifestError(f"Node '{nickname}' directory does not exist: {directory}")

    target_path = directory / target
    if not target_path.is_file():
        raise ManifestError(f"Node '{nickname}' target does not exist: {target_path}")

    parameters = _as_str_map(body.get("parameters"), nickname)
    return NodeSpec(
        nickname=nickname,
        directory=directory,
        target=target,
        parameters=parameters,
    )


def load_and_validate_manifest(path_str: str) -> Manifest:
    """Parse YAML and validate EE-11 rules. Raises ManifestError on failure."""
    path = resolve_manifest_path(path_str)
    if not path.is_file():
        raise ManifestError(f"Manifest file not found: {path}")

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ManifestError(f"Manifest is not parseable YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise ManifestError("Manifest root must be a mapping")
    nodes_raw = data.get("nodes")
    if not isinstance(nodes_raw, list) or not nodes_raw:
        raise ManifestError("Manifest must have a non-empty nodes: list")

    nodes = [_parse_node_entry(item) for item in nodes_raw]
    return Manifest(path=path, nodes=nodes)
