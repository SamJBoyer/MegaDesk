"""Graph files are validated before they replace the open board."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from engine.graph_model import (
    GraphError,
    boot_graph_path,
    read_graph_document,
    read_last_graph_path,
    remember_last_graph,
)


def test_read_graph_document_rejects_a_random_json(tmp_path: Path) -> None:
    path = tmp_path / "package.json"
    path.write_text('{"name": "not-a-graph", "version": "1.0"}', encoding="utf-8")
    with pytest.raises(GraphError, match="no 'members'"):
        read_graph_document(path)


def test_read_graph_document_rejects_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(GraphError, match="not valid JSON"):
        read_graph_document(path)


def test_read_graph_document_rejects_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(GraphError, match="no such file"):
        read_graph_document(tmp_path / "gone.json")


def test_read_graph_document_rejects_an_unknown_member_type(tmp_path: Path) -> None:
    path = tmp_path / "graph.json"
    path.write_text(
        json.dumps({"members": {"m1": {"type": "unknown", "node_name": "x"}}}),
        encoding="utf-8",
    )
    with pytest.raises(GraphError, match="unknown member type"):
        read_graph_document(path)


def test_read_graph_document_accepts_parameters(tmp_path: Path) -> None:
    path = tmp_path / "graph.json"
    path.write_text(
        json.dumps(
            {
                "members": {
                    "m1": {
                        "type": "megadesk",
                        "node_name": "work_dispatcher",
                        "position": [1, 2],
                        "parameters": {"GIT_URL": "https://github.com/acme/widgets"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    records = read_graph_document(path)
    assert records["m1"]["node_name"] == "work_dispatcher"
    assert records["m1"]["parameters"]["GIT_URL"] == "https://github.com/acme/widgets"
    assert records["m1"]["member_id"] == "m1"


def test_remember_last_graph_round_trips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import engine.graph_model as gm

    monkeypatch.setattr(gm, "GRAPHS_DIR", tmp_path)
    monkeypatch.setattr(gm, "DEFAULT_GRAPH_PATH", tmp_path / "default.json")
    graph = tmp_path / "core.json"
    graph.write_text(json.dumps({"members": {}}), encoding="utf-8")

    remember_last_graph(graph)
    assert read_last_graph_path() == graph.resolve()
    assert boot_graph_path() == graph.resolve()


def test_boot_graph_path_falls_back_when_pointer_is_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import engine.graph_model as gm

    default = tmp_path / "default.json"
    monkeypatch.setattr(gm, "GRAPHS_DIR", tmp_path)
    monkeypatch.setattr(gm, "DEFAULT_GRAPH_PATH", default)
    (tmp_path / "CURRENT").write_text(
        json.dumps({"path": str(tmp_path / "gone.json")}),
        encoding="utf-8",
    )
    assert boot_graph_path() == default


def test_boot_graph_path_ignores_a_non_graph_pointer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import engine.graph_model as gm

    default = tmp_path / "default.json"
    junk = tmp_path / "package.json"
    junk.write_text('{"name": "not-a-graph"}', encoding="utf-8")
    monkeypatch.setattr(gm, "GRAPHS_DIR", tmp_path)
    monkeypatch.setattr(gm, "DEFAULT_GRAPH_PATH", default)
    remember_last_graph(junk)
    assert read_last_graph_path() is None
    assert boot_graph_path() == default
