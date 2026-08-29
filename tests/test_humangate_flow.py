"""Human gates: WorkDispatcher still tracks issue labels; AutoIntegrate tracks PRs.

AutoIntegrate is the reason both factories learned an optional ``ref``. A merge
conflict lives on the pull request's branch, so an order that starts from ``dev``
produces a fix nobody can merge. These tests cross the whole path a gate uses to
learn that branch: the ``mergeable`` check merge-check posts, the PR
list in ``megadesk_contracts.human_gate``, and the order that comes out the
other side.

`FakeGh` stands in for GitHub. Everything else is real.

Scenario numbers match the table in `Docs/integration_testing.md`.
"""

from __future__ import annotations

import pytest
from conftest import (
    CLOUDORDER_CANONICAL_FIELDS,
    WORKORDER_CANONICAL_FIELDS,
)

pytestmark = [pytest.mark.canvas, pytest.mark.redis]

REPO_URL = "https://github.com/acme/widgets"
REPO = "widgets"
PR_URL = "https://github.com/acme/widgets/pull/12"
BRANCH = "cursor/add-widget-tests-ed2e"


def connect_gate(harness, node: str, url: str = REPO_URL):
    driver = harness.drop(node)
    driver.type_into("git_url", url)
    return driver


def press_row(harness, gate, issue_id: int, *, factory: str | None = None):
    harness.wait_for_widget(gate, f"issue_btn_{issue_id}")
    harness.wait_until(
        lambda: gate.label(f"issue_btn_{issue_id}") != "",
        message="the row to know which branch it is about",
    )
    if factory is not None:
        gate.select(f"issue_factory_{issue_id}", factory)
    gate.click(f"issue_btn_{issue_id}")


# --- H1: the target label ---------------------------------------------------


def test_h1_the_gate_offers_the_labels_the_repo_actually_has(
    fake_gh, harness
) -> None:
    """A gate that only ever showed its default could not be retargeted."""
    fake_gh.labels = ["agent-ready", "docs", "needs-a-human"]

    gate = connect_gate(harness, "work_dispatcher")

    harness.wait_until(
        lambda: "docs" in gate.items("label_combo"),
        message="the label dropdown to fill from the repo",
    )
    assert gate.get("label_combo") == "agent-ready"


def test_h1b_retargeting_the_label_changes_which_issues_are_listed(
    fake_gh, harness
) -> None:
    fake_gh.labels = ["agent-ready", "docs"]
    fake_gh.add_issue(41, "add-widget-tests", "Cover the widget module.")
    fake_gh.add_issue(50, "stuck", "A docs ticket.", labels=("docs",))

    gate = connect_gate(harness, "work_dispatcher")
    harness.wait_for_widget(gate, "ticket_btn_41")

    gate.select("label_combo", "docs")

    harness.wait_for_widget(gate, "ticket_btn_50")
    assert not gate.exists("ticket_btn_41"), (
        "rows from the previous label must go with it"
    )


# --- H2: PR → branch → order ref -------------------------------------------


def test_h2_auto_integrate_orders_a_fix_on_the_pull_requests_branch(
    fake_gh, harness, workorders
) -> None:
    """The whole point: the agent has to start on the branch that is stuck."""
    fake_gh.add_merge_fail(12, "stuck", PR_URL, branch=BRANCH)

    gate = connect_gate(harness, "auto_integrate")
    press_row(harness, gate, 12)

    entries = workorders()
    assert len(entries) == 1, f"expected one WORKORDER, got {entries}"
    _entry_id, fields = entries[0]
    assert set(fields) == set(WORKORDER_CANONICAL_FIELDS)
    assert fields["ref"] == BRANCH
    assert fields["repo"] == REPO
    assert fields["URL"] == REPO_URL
    assert "12" in fields["ticket_name"]
    assert BRANCH in fields["instructions"]
    assert "dev" in fields["instructions"]


def test_h2b_the_cloud_factory_is_told_the_same_branch(
    fake_gh, harness, cloudorders, workorders
) -> None:
    fake_gh.add_merge_fail(12, "stuck", PR_URL, branch=BRANCH)

    gate = connect_gate(harness, "auto_integrate")
    press_row(harness, gate, 12, factory="cloud")

    orders = cloudorders()
    assert len(orders) == 1, f"expected one CLOUDORDER, got {orders}"
    _entry_id, fields = orders[0]
    assert set(fields) == set(CLOUDORDER_CANONICAL_FIELDS)
    assert fields["ref"] == BRANCH
    assert workorders() == []


def test_h2c_the_row_shows_the_branch_rather_than_the_issue_number(
    fake_gh, harness
) -> None:
    fake_gh.add_merge_fail(12, "stuck", PR_URL, branch=BRANCH)

    gate = connect_gate(harness, "auto_integrate")
    harness.wait_for_widget(gate, "issue_btn_12")
    harness.wait_until(
        lambda: BRANCH in gate.label("issue_btn_12"),
        message="the row label to name the branch",
    )


# --- H3: a PR with no head branch is not dispatchable ----------------------


def test_h3_a_pull_request_without_a_branch_is_not_dispatchable(
    fake_gh, harness, workorders
) -> None:
    fake_gh.add_pull_request(
        12, head="", url=PR_URL, title="stuck", merge_check="failure"
    )

    gate = connect_gate(harness, "auto_integrate")
    harness.wait_for_widget(gate, "issue_btn_12")
    harness.pump(3)

    assert not gate.enabled("issue_btn_12")
    assert workorders() == []


# --- H4: success and failure are opposite queues ---------------------------


def test_h4_a_mergeable_pr_is_not_on_auto_integrate(fake_gh, harness) -> None:
    fake_gh.add_merge_success(12, "clean", PR_URL, branch=BRANCH)
    fake_gh.add_merge_fail(13, "stuck", "https://github.com/acme/widgets/pull/13")

    gate = connect_gate(harness, "auto_integrate")
    harness.wait_for_widget(gate, "issue_btn_13")
    assert not gate.exists("issue_btn_12")


def test_h4b_list_merge_prs_splits_success_and_failure() -> None:
    from megadesk_contracts.human_gate import (
        MERGE_CHECK_FAILURE,
        MERGE_CHECK_SUCCESS,
        list_merge_prs,
    )
    from megadesk_contracts.testing import FakeGh

    gh = FakeGh()
    gh.add_merge_success(12, "clean", PR_URL, branch=BRANCH)
    gh.add_merge_fail(13, "stuck", "https://github.com/acme/widgets/pull/13")

    ok, success, err = list_merge_prs("acme", "widgets", MERGE_CHECK_SUCCESS, gh=gh)
    assert ok and not err
    assert [pr.number for pr in success] == [12]
    assert success[0].branch == BRANCH

    ok, failed, err = list_merge_prs("acme", "widgets", MERGE_CHECK_FAILURE, gh=gh)
    assert ok and not err
    assert [pr.number for pr in failed] == [13]


# --- H5: the default branch stays the default -------------------------------


def test_h5_work_dispatcher_leaves_the_ref_empty(
    fake_gh, harness, workorders
) -> None:
    """An empty ``ref`` is what makes ``dev`` the fallback for every other order."""
    from megadesk_contracts.wire.factory import DEFAULT_STARTING_REF
    from megadesk_contracts.wire.machine import parse_workorder

    fake_gh.add_issue(41, "add-widget-tests", "Cover the widget module.")

    gate = connect_gate(harness, "work_dispatcher")
    harness.wait_for_widget(gate, "ticket_btn_41")
    gate.click("ticket_btn_41")

    entries = workorders()
    assert len(entries) == 1
    assert parse_workorder(entries[0][1])["ref"] == ""
    assert DEFAULT_STARTING_REF == "dev"
