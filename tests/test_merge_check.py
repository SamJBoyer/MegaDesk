"""The ``mergeable`` check is the only mergeability signal.

``.github/workflows/merge-check.yml`` posts a check named
``mergeable`` onto each PR head. AutoIntegrate and PRManager both read that
through ``merge_check_verdict`` / ``list_merge_prs``. The Actions job itself is
named ``report`` so it cannot masquerade as the signal after ``dev`` moves.
"""

from __future__ import annotations

from megadesk_contracts.human_gate import (
    MERGE_CHECK_CONTEXT,
    MERGE_CHECK_FAILURE,
    MERGE_CHECK_SUCCESS,
    merge_check_verdict,
)


def test_a_success_status_is_success() -> None:
    assert (
        merge_check_verdict(
            [
                {
                    "__typename": "StatusContext",
                    "context": MERGE_CHECK_CONTEXT,
                    "state": "SUCCESS",
                    "startedAt": "2026-01-01T00:00:00Z",
                }
            ]
        )
        == MERGE_CHECK_SUCCESS
    )


def test_a_failure_status_is_failure() -> None:
    assert (
        merge_check_verdict(
            [
                {
                    "__typename": "StatusContext",
                    "context": MERGE_CHECK_CONTEXT,
                    "state": "FAILURE",
                    "startedAt": "2026-01-01T00:00:00Z",
                }
            ]
        )
        == MERGE_CHECK_FAILURE
    )


def test_the_newest_mergeable_signal_wins() -> None:
    """A push to dev re-posts onto the same SHA; an older Actions check is stale."""
    rollup = [
        {
            "__typename": "CheckRun",
            "name": MERGE_CHECK_CONTEXT,
            "conclusion": "SUCCESS",
            "completedAt": "2026-01-01T00:00:00Z",
        },
        {
            "__typename": "StatusContext",
            "context": MERGE_CHECK_CONTEXT,
            "state": "FAILURE",
            "startedAt": "2026-01-02T00:00:00Z",
        },
    ]
    assert merge_check_verdict(rollup) == MERGE_CHECK_FAILURE


def test_a_report_job_does_not_count() -> None:
    assert (
        merge_check_verdict(
            [
                {
                    "__typename": "CheckRun",
                    "name": "report",
                    "conclusion": "SUCCESS",
                    "completedAt": "2026-01-03T00:00:00Z",
                },
                {
                    "__typename": "StatusContext",
                    "context": MERGE_CHECK_CONTEXT,
                    "state": "FAILURE",
                    "startedAt": "2026-01-01T00:00:00Z",
                },
            ]
        )
        == MERGE_CHECK_FAILURE
    )


def test_cancelled_and_pending_are_ignored() -> None:
    assert merge_check_verdict([]) is None
    assert (
        merge_check_verdict(
            [
                {
                    "__typename": "CheckRun",
                    "name": MERGE_CHECK_CONTEXT,
                    "conclusion": "CANCELLED",
                    "completedAt": "2026-01-02T00:00:00Z",
                },
                {
                    "__typename": "StatusContext",
                    "context": MERGE_CHECK_CONTEXT,
                    "state": "PENDING",
                    "startedAt": "2026-01-03T00:00:00Z",
                },
            ]
        )
        is None
    )
