"""The vocabulary every Factory shares, whatever infrastructure it runs on.

A Factory reads orders, builds somewhere for an agent to work, and reports where
the run got to. MachineFactory does that with a git worktree and a container it
fully controls; CloudFactory does it with a repo URL and a Cursor-hosted VM it
does not. Those are different enough to deserve separate nodes and separate
streams, but a graph that distributes work across both should not have to learn
two words for "this run failed".

So the status set lives here, once, and both ``wire.machine`` and ``wire.cloud``
import it. A graph controller can read a status off either family and branch on
it without asking which kind of factory produced it.

The one distinction worth keeping is ``startup_error`` versus ``error``: a run
that never started may be retried, a run that started and failed may not,
because retrying it would duplicate whatever it already did. See
``megadesk_contracts.agent_errors`` for the exceptions a runtime raises to say
which happened.
"""

from __future__ import annotations

from typing import Any

DEFAULT_MODEL = "auto"

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_FINISHED = "finished"
STATUS_ERROR = "error"
STATUS_CANCELLED = "cancelled"
STATUS_STARTUP_ERROR = "startup_error"

RUN_STATUSES = frozenset(
    {
        STATUS_QUEUED,
        STATUS_RUNNING,
        STATUS_FINISHED,
        STATUS_ERROR,
        STATUS_CANCELLED,
        STATUS_STARTUP_ERROR,
    }
)
TERMINAL_STATUSES = frozenset(
    {STATUS_FINISHED, STATUS_ERROR, STATUS_CANCELLED, STATUS_STARTUP_ERROR}
)


def is_terminal(status: str) -> bool:
    """Whether a run has stopped moving, so a graph can stop watching it."""
    return str(status).strip() in TERMINAL_STATUSES


# Cursor's own status vocabulary, mapped onto ours. Both factories drive agents
# through ``cursor_sdk`` — one locally, one in the cloud — so both get these
# spellings back and both have to land on the same word for the same outcome.
_PROVIDER_STATUSES = {
    "finished": STATUS_FINISHED,
    "completed": STATUS_FINISHED,
    "success": STATUS_FINISHED,
    "error": STATUS_ERROR,
    "failed": STATUS_ERROR,
    "expired": STATUS_ERROR,
    "cancelled": STATUS_CANCELLED,
    "canceled": STATUS_CANCELLED,
    "running": STATUS_RUNNING,
    "creating": STATUS_RUNNING,
    "pending": STATUS_RUNNING,
    "queued": STATUS_RUNNING,
}


def normalize_status(raw: Any) -> str:
    """Translate a provider status into one of ours.

    Anything unrecognized is treated as still running. Guessing ``finished``
    would close a run that is still writing to a branch, and a run wrongly held
    open corrects itself on the next poll while one wrongly closed does not.
    """
    return _PROVIDER_STATUSES.get(str(raw or "").strip().lower(), STATUS_RUNNING)
