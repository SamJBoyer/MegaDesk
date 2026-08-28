"""The node-to-node workflow: WorkDispatcher → MachineFactory → PRManager.

Each test crosses a seam that unit tests on either side cannot reach — a Redis
stream, a GitHub issue list, or a GUI callback. The chain is cut at the sandbox
boundary: `FakeAgent` stands in for Floor cloning, Docker and `cursor_sdk`, while
the GUI, the stream contracts, the consumer-group semantics and `FakeGh` stay
real.

PRManager no longer reads `FINISHED:<repo>`. It connects to the same `git_url`
WorkDispatcher uses and lists open issues labeled `merge_success`.

The real MachineFactory BE is never launched. Dropping a node only publishes a
LAUNCHREQUEST when Supervisor is alive, and neither FE under test has a BE, so
nothing here can spawn Docker.

Scenario numbers match the table in `Docs/integration_testing.md`.
"""

from __future__ import annotations

import pytest
from conftest import (
    CLOUDORDER_CANONICAL_FIELDS,
    FINISHED_CANONICAL_FIELDS,
    WORKORDER_CANONICAL_FIELDS,
    WORKORDER_STREAM,
)

pytestmark = [pytest.mark.canvas, pytest.mark.redis, pytest.mark.git]

REPO_URL = "https://github.com/acme/widgets"
REPO = "widgets"
PR_URL = "https://github.com/acme/widgets/pull/1"


# --- helpers ---------------------------------------------------------------


def connect_dispatcher(harness, url: str = REPO_URL):
    """Drop WorkDispatcher and point it at a repo, as an operator would."""
    dispatcher = harness.drop("work_dispatcher")
    dispatcher.type_into("git_url", url)
    return dispatcher


def connect_manager(harness, url: str = REPO_URL):
    """Drop PRManager and point it at a repo, as an operator would."""
    manager = harness.drop("pr_manager")
    manager.type_into("git_url", url)
    return manager


def dispatch(
    harness,
    dispatcher,
    issue_id: int,
    *,
    model: str | None = None,
    factory: str | None = None,
) -> None:
    """Wait for the issue row to appear, optionally pick factory/model, then press it."""
    harness.wait_for_widget(dispatcher, f"ticket_btn_{issue_id}")
    if factory is not None:
        dispatcher.select(f"ticket_factory_{issue_id}", factory)
    if model is not None:
        dispatcher.select(f"ticket_model_{issue_id}", model)
    dispatcher.click(f"ticket_btn_{issue_id}")


# --- T1 / T2: WorkDispatcher → WORKORDER ---------------------------------


def test_t1_dispatch_writes_the_canonical_workorder(
    redis_client, fake_gh, harness, workorders
) -> None:
    """Catches field renames and extra or missing WORKORDER keys."""
    from megadesk_contracts.wire import cloud as cloud_wire

    fake_gh.add_issue(41, "add-widget-tests", "Cover the widget module with tests.")

    dispatcher = connect_dispatcher(harness)
    dispatch(harness, dispatcher, 41)

    entries = workorders()
    assert len(entries) == 1, f"expected one WORKORDER, got {entries}"
    _entry_id, fields = entries[0]

    assert set(fields) == set(WORKORDER_CANONICAL_FIELDS), (
        "WORKORDER must carry exactly the canonical field names; "
        f"got {sorted(fields)}"
    )
    assert fields["repo"] == REPO
    assert fields["URL"] == REPO_URL
    assert fields["auto_pr"] == "true"
    assert fields["ticket_name"] == "add-widget-tests"
    assert fields["instructions"] == "Cover the widget module with tests."
    assert fields["model"] == "auto"
    assert redis_client.xlen(cloud_wire.CLOUDORDER_STREAM) == 0


def test_t1c_dispatch_to_cloud_writes_a_canonical_cloudorder(
    fake_gh, harness, workorders, read_stream
) -> None:
    """The per-row factory combo sends the ticket to one factory, not both."""
    from megadesk_contracts.wire import cloud as cloud_wire

    fake_gh.add_issue(41, "add-widget-tests", "Cover the widget module with tests.")

    dispatcher = connect_dispatcher(harness)
    dispatch(harness, dispatcher, 41, factory="cloud")

    orders = read_stream(cloud_wire.CLOUDORDER_STREAM)
    assert len(orders) == 1, f"expected one CLOUDORDER, got {orders}"
    _entry_id, fields = orders[0]
    assert set(fields) == set(CLOUDORDER_CANONICAL_FIELDS)
    assert fields["repo_url"] == REPO_URL
    assert fields["title"] == "add-widget-tests"
    assert fields["instructions"] == "Cover the widget module with tests."
    assert fields["auto_pr"] == "true"
    assert fields["model"] == "auto"
    assert workorders() == []


def test_t1b_issue_without_a_body_falls_back_to_its_title(
    redis_client, fake_gh, harness, workorders
) -> None:
    fake_gh.add_issue(7, "bodyless-ticket", "")

    dispatcher = connect_dispatcher(harness)
    dispatch(harness, dispatcher, 7)

    _entry_id, fields = workorders()[0]
    assert fields["instructions"] == "bodyless-ticket"


def test_t2_per_row_model_combo_reaches_the_payload(
    redis_client, fake_gh, harness, workorders, read_stream
) -> None:
    """Catches per-row widget → payload wiring."""
    from megadesk_contracts.wire import cloud as cloud_wire

    fake_gh.add_issue(42, "pick-a-model", "Use the fast model.")

    dispatcher = connect_dispatcher(harness)
    dispatch(harness, dispatcher, 42, model="grok-4.5")

    _entry_id, fields = workorders()[0]
    assert fields["model"] == "grok-4.5"
    assert read_stream(cloud_wire.CLOUDORDER_STREAM) == []


def test_t2b_gh_failure_surfaces_in_the_status_widget(
    redis_client, fake_gh, harness
) -> None:
    fake_gh.repo_error = "could not resolve to a Repository"

    dispatcher = connect_dispatcher(harness)

    harness.wait_until(
        lambda: fake_gh.repo_error in dispatcher.get("status_text"),
        message="the gh error to reach the status widget",
    )
    assert not dispatcher.exists("ticket_btn_1")


# --- T3: WORKORDER → sandbox boundary → FINISHED ---------------------------


def test_t3_agent_acks_the_group_and_publishes_finished(
    redis_client, fake_gh, harness, git_floor, fake_agent, machine_wire
) -> None:
    """Catches consumer-group and ack semantics, and FINISHED field shape."""
    fake_gh.add_issue(43, "t3-ticket", "Make a commit.")

    dispatcher = connect_dispatcher(harness)
    dispatch(harness, dispatcher, 43)

    runs = fake_agent.run_once()
    assert len(runs) == 1, "the machine_factory group delivered nothing"
    run = runs[0]
    assert run.repo == REPO
    assert fake_agent.pending() == 0, "WORKORDER entries left unacked after handling"

    finished = redis_client.xrange(machine_wire.finished_stream(REPO))
    assert len(finished) == 1
    _finished_id, fields = finished[0]

    assert set(fields) == set(FINISHED_CANONICAL_FIELDS), (
        f"FINISHED must carry exactly the canonical fields; got {sorted(fields)}"
    )
    assert fields["ticket_name"] == "t3-ticket"
    assert fields["ticket_id"] == run.workorder_id
    assert fields["status"] == machine_wire.STATUS_FINISHED
    assert fields["pr_url"].startswith("https://")
    assert run.pr_url == fields["pr_url"]


def test_t3b_a_second_pass_delivers_nothing_new(
    redis_client, fake_gh, harness, fake_agent, machine_wire
) -> None:
    """An acked entry must not be redelivered on the next poll."""
    fake_gh.add_issue(44, "t3b-ticket", "Once only.")

    dispatcher = connect_dispatcher(harness)
    dispatch(harness, dispatcher, 44)

    assert len(fake_agent.run_once()) == 1
    assert fake_agent.run_once() == []
    assert redis_client.xlen(machine_wire.finished_stream(REPO)) == 1


# --- T4: merge_success issues → PRManager GUI --------------------------------


def test_t4_merge_success_issue_populates_a_pr_row(fake_gh, harness) -> None:
    """Catches GitHub list → GUI population, which depends on the frame-pump drain."""
    pr_url = "https://github.com/acme/widgets/pull/4"
    fake_gh.add_merge_success(4, "t4-ticket", pr_url)
    manager = connect_manager(harness)

    harness.wait_for_widget(manager, "name::4")
    label = manager.get("name::4")
    assert "t4-ticket" in label
    assert "pull/4" in label
    assert manager.exists("open_pr::4")
    assert manager.enabled("open_pr::4")
    assert manager.exists("pull::4")
    assert manager.enabled("pull::4")
    assert manager.exists("vscode::4")
    assert manager.enabled("vscode::4")
    assert manager.exists("cursor::4")
    assert manager.enabled("cursor::4")
    assert manager.shown("dismiss::4")


def test_t4b_agent_ready_issue_is_not_shown(fake_gh, harness) -> None:
    """PRManager must not render WorkDispatcher's agent-ready queue."""
    fake_gh.add_issue(4, "agent-only", "Cover the widget module.")
    manager = connect_manager(harness)

    harness.wait_until(
        lambda: "merge_success" in manager.get("status_text"),
        message="PRManager to connect and list merge_success issues",
    )
    assert manager.suffixes(r"^name::") == []


# --- T5 / T7: open-PR affordance and dismiss cleanup ----------------------


def test_t5_open_pr_disabled_without_url(fake_gh, harness) -> None:
    """A merge_success issue with no PR link must keep the button disabled."""
    fake_gh.add_issue(5, "t5-ticket", "no pull request yet", labels=("MERGE_SUCCESS",))
    manager = connect_manager(harness)

    harness.wait_for_widget(manager, "name::5")
    assert not manager.enabled("open_pr::5")
    assert not manager.enabled("pull::5")
    assert not manager.enabled("vscode::5")
    assert not manager.enabled("cursor::5")
    assert "t5-ticket" in manager.get("name::5")
    assert manager.shown("dismiss::5")


def test_t7_dismiss_closes_the_issue_and_removes_the_row(fake_gh, harness) -> None:
    """Catches GitHub close drifting apart from GUI teardown."""
    issue = fake_gh.add_merge_success(7, "t7-ticket", PR_URL)
    manager = connect_manager(harness)
    harness.wait_for_widget(manager, "name::7")
    assert manager.shown("dismiss::7")

    manager.click("dismiss::7")
    harness.pump(2)

    assert fake_gh.issue_closes == 1, "dismiss must gh issue close"
    assert issue.state == "closed"
    assert not manager.exists("name::7"), "the row widget outlived its issue"
    assert not manager.exists("open_pr::7")
    assert not manager.exists("pull::7")
    assert not manager.exists("vscode::7")
    assert not manager.exists("cursor::7")


# --- T8: the whole chain over one shared frame pump ------------------------


def test_t8_full_chain_from_dispatch_to_merge_success_pr(
    redis_client, fake_gh, harness, fake_agent, machine_wire
) -> None:
    """Dispatch → FakeAgent, and a merge_success row, with both FEs hosted.

    The only scenario where two FEs share pump state, which is the exact
    condition under which the frame-pump blockers manifest. PRManager reads
    GitHub, not FINISHED, so the two sides of the board are independent inputs
    on one pump.
    """
    fake_gh.add_issue(88, "t8-ticket", "Do the thing.")
    fake_gh.add_merge_success(89, "t8-pr", PR_URL)

    dispatcher = connect_dispatcher(harness)
    manager = connect_manager(harness)

    dispatch(harness, dispatcher, 88)
    assert redis_client.xlen(WORKORDER_STREAM) == 1

    runs = fake_agent.run_once()
    assert len(runs) == 1
    run = runs[0]
    assert redis_client.xlen(machine_wire.finished_stream(REPO)) == 1

    harness.wait_for_widget(manager, "name::89")

    # WorkDispatcher must still be draining while PRManager works: both
    # depend on the same shared pump, and its status is written by its own
    # background thread.
    harness.wait_until(
        lambda: "agent-ready" in dispatcher.get("status_text"),
        message="WorkDispatcher to keep draining alongside PRManager",
    )
    assert dispatcher.exists("ticket_btn_88")

    label = manager.get("name::89")
    assert "t8-pr" in label
    assert run.pr_url
    assert manager.enabled("open_pr::89")
    assert manager.enabled("pull::89")
    assert manager.enabled("vscode::89")
    assert manager.enabled("cursor::89")
    assert manager.shown("dismiss::89")
    harness.screenshot("t8-merge-success-pr")


# --- T9: pull button → PRManager Scope ------------------------------------


def _route_pulls_to_floor(monkeypatch, git_floor) -> None:
    """Keep the GitHub URL in the GUI; clone from the local Floor origin."""
    import pr_manager_app

    original = pr_manager_app.pull_pr

    def routed(*, url: str, repo: str, pr_number: int, root=None):
        return original(
            url=str(git_floor.origin),
            repo=repo,
            pr_number=pr_number,
            root=root,
        )

    monkeypatch.setattr(pr_manager_app, "pull_pr", routed)


def test_t9_pull_clones_the_pr_into_scope(
    fake_gh, harness, git_floor, monkeypatch, pr_scope_root
) -> None:
    """Catches the pull button wiring to a gitignored Scope checkout."""
    from test_pr_manager_scope import publish_pull_ref

    publish_pull_ref(git_floor, 4)
    fake_gh.add_merge_success(4, "t9-ticket", "https://github.com/acme/widgets/pull/4")
    _route_pulls_to_floor(monkeypatch, git_floor)

    manager = connect_manager(harness)
    harness.wait_for_widget(manager, "pull::4")
    assert manager.enabled("vscode::4")
    assert manager.enabled("cursor::4")

    manager.click("pull::4")
    dest = pr_scope_root / "widgets" / "pr-4"
    harness.wait_until(
        lambda: (dest / "pr.txt").is_file(),
        message="PR checkout to land under PR_SCOPE_ROOT",
    )
    assert (dest / ".git").exists()
    assert (dest / "pr.txt").read_text(encoding="utf-8") == "changes from pr 4\n"
    harness.wait_until(
        lambda: "Pulled pr-4" in manager.get("status_text"),
        message="status to report the scoped checkout",
    )
    harness.screenshot("t9-pulled-pr")


def test_t9b_a_second_pull_resets_the_same_checkout(
    fake_gh, harness, git_floor, monkeypatch, pr_scope_root
) -> None:
    """A later PR head must replace the files already in Scope."""
    from megadesk_contracts.testing import git
    from test_pr_manager_scope import publish_pull_ref

    publish_pull_ref(git_floor, 4, text="first\n")
    fake_gh.add_merge_success(4, "t9b-ticket", "https://github.com/acme/widgets/pull/4")
    _route_pulls_to_floor(monkeypatch, git_floor)

    manager = connect_manager(harness)
    harness.wait_for_widget(manager, "pull::4")
    manager.click("pull::4")
    dest = pr_scope_root / "widgets" / "pr-4"
    harness.wait_until(
        lambda: (dest / "pr.txt").is_file()
        and (dest / "pr.txt").read_text(encoding="utf-8") == "first\n",
        message="first PR head to land in Scope",
    )

    ticket = git_floor.ticket_dir("pr-4")
    git_floor.commit(ticket, "pr.txt", "second\n", "pr 4 follow-up")
    git("push", str(git_floor.origin), "HEAD:refs/pull/4/head", cwd=ticket)

    manager.click("pull::4")
    harness.wait_until(
        lambda: (dest / "pr.txt").read_text(encoding="utf-8") == "second\n",
        message="second pull to hard-reset onto the new PR head",
    )
