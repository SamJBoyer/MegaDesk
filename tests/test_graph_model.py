"""Graph files are validated before they replace the open board."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from engine.graph_model import GraphError, read_graph_document


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
        json.dumps({"members": {"m1": {"type": "legacy", "node_name": "x"}}}),
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
                        "node_name": "ticket_dispatcher",
                        "position": [1, 2],
                        "parameters": {"GIT_URL": "https://github.com/acme/widgets"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    records = read_graph_document(path)
    assert records["m1"]["node_name"] == "ticket_dispatcher"
    assert records["m1"]["parameters"]["GIT_URL"] == "https://github.com/acme/widgets"
    assert records["m1"]["member_id"] == "m1"
