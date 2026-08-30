"""Graph parameter helpers: yaml names, coercion, Redis/env JSON packet."""

from __future__ import annotations

from pathlib import Path

from megadesk_contracts.parameters import (
    ENV_PARAMETERS,
    coerce_parameters,
    load_parameter_names,
    normalize_parameters,
    parameters_from_env,
    parameters_to_json,
)


def test_load_parameter_names_reads_a_commented_list(tmp_path: Path) -> None:
    (tmp_path / "parameters.yaml").write_text(
        "- GIT_URL # the http of the git repo this node will connect to\n"
        "- OTHER\n"
        "# ignored\n",
        encoding="utf-8",
    )
    (tmp_path / "node.py").write_text("#\n", encoding="utf-8")
    assert load_parameter_names(tmp_path / "node.py") == ("GIT_URL", "OTHER")


def test_load_parameter_names_is_empty_when_the_file_is_missing(tmp_path: Path) -> None:
    assert load_parameter_names(tmp_path / "node.py") == ()


def test_human_gates_declare_their_parameters() -> None:
    import auto_integrate_node
    import work_dispatcher_node

    assert load_parameter_names(work_dispatcher_node.__file__) == (
        "GIT_URL",
        "ISSUE_LABEL",
        "MAX_DEPTH",
    )
    assert load_parameter_names(auto_integrate_node.__file__) == ("GIT_URL",)


def test_normalize_parameters_keeps_declared_names_only() -> None:
    assert normalize_parameters(
        {"GIT_URL": "https://github.com/acme/widgets", "NOPE": "1"},
        ("GIT_URL",),
    ) == {"GIT_URL": "https://github.com/acme/widgets"}


def test_parameters_json_roundtrip() -> None:
    blob = parameters_to_json({"GIT_URL": "https://x"})
    assert coerce_parameters(blob) == {"GIT_URL": "https://x"}
    assert parameters_to_json({}) == ""
    assert coerce_parameters("") == {}


def test_parameters_from_env_reads_the_supervisor_injection() -> None:
    assert parameters_from_env({ENV_PARAMETERS: '{"GIT_URL": "https://x"}'}) == {
        "GIT_URL": "https://x"
    }
    assert parameters_from_env({}) == {}
