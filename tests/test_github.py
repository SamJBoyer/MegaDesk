"""GitHub URL parse, ``gh`` CLI, and labeled-issue poll live in one place."""

from __future__ import annotations

import pytest
from megadesk_contracts.github import (
    canonical_github_repo,
    list_github_issues,
    normalize_repo_url,
    parse_github_repo,
    resolve_github_remote,
    run_gh,
)
from megadesk_contracts.testing import FakeGh


def test_parse_github_repo_accepts_https_ssh_host_and_slug() -> None:
    expected = ("acme", "widgets")
    assert parse_github_repo("https://github.com/acme/widgets.git") == expected
    assert parse_github_repo("git@github.com:acme/widgets.git") == expected
    assert parse_github_repo("github.com/acme/widgets") == expected
    assert parse_github_repo("acme/widgets") == expected
    assert parse_github_repo("https://github.com/acme/widgets/pull/7") == expected
    assert parse_github_repo("") is None
    assert parse_github_repo("https://gitlab.com/acme/widgets") is None


def test_canonical_github_repo_uses_the_repo_name_not_owner_slash_name() -> None:
    url, name = canonical_github_repo("https://github.com/SamJBoyer/SMOKETESTREPO.git")
    assert name == "SMOKETESTREPO"
    assert "/" not in name
    assert url == "https://github.com/SamJBoyer/SMOKETESTREPO"
    slug_url, slug_name = canonical_github_repo("SamJBoyer/SMOKETESTREPO")
    assert (slug_url, slug_name) == (url, name)


def test_canonical_github_repo_passes_local_paths_through(tmp_path) -> None:
    path = str(tmp_path / "origin.git")
    url, name = canonical_github_repo(path)
    assert url == path
    assert name == "origin"


def test_resolve_github_remote_status_lines() -> None:
    assert resolve_github_remote("") == (None, "Enter a GitHub repository URL")
    remote, err = resolve_github_remote("https://example.com/acme/widgets")
    assert remote is None
    assert err == "Unsupported URL (GitHub https or SSH required)"
    remote, err = resolve_github_remote("git@github.com:acme/widgets.git")
    assert err is None
    assert remote == ("acme", "widgets", "https://github.com/acme/widgets")


def test_normalize_repo_url_is_https_owner_repo() -> None:
    assert (
        normalize_repo_url("git@github.com:acme/widgets.git", "acme", "widgets")
        == "https://github.com/acme/widgets"
    )


def test_list_github_issues_views_the_repo_then_filters_by_label() -> None:
    gh = FakeGh()
    gh.add_issue(1, "ready", labels=("agent-ready",))
    gh.add_merge_success(2, "merged", "https://github.com/acme/widgets/pull/2")
    ok, items, err = list_github_issues("acme", "widgets", "agent-ready", gh=gh)
    assert ok and err is None
    assert [item["number"] for item in items] == [1]
    assert gh.repo_views == 1
    assert gh.issue_lists == 1


def test_list_github_issues_reports_a_missing_remote() -> None:
    gh = FakeGh(repo_error="Could not resolve")
    ok, items, err = list_github_issues("acme", "nope", "agent-ready", gh=gh)
    assert not ok
    assert items == []
    assert err == "Could not resolve"


def test_nodes_reexport_the_shared_github_helpers() -> None:
    pytest.importorskip("dearpygui")
    pytest.importorskip("redis")
    import pr_manager_app
    import ticket_dispatcher_app
    from CloudFactoryManager.runtime import canonical_github_repo as cloud_canonical

    assert ticket_dispatcher_app.parse_github_repo is parse_github_repo
    assert ticket_dispatcher_app.normalize_repo_url is normalize_repo_url
    assert ticket_dispatcher_app.run_gh is run_gh
    assert pr_manager_app.parse_github_repo is parse_github_repo
    assert pr_manager_app.run_gh is run_gh
    assert cloud_canonical is canonical_github_repo
