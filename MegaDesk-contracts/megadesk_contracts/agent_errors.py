"""The two agent failures every node has to keep apart.

The Cursor SDK draws this line and it is worth preserving anywhere a node drives
an agent: a thrown ``CursorAgentError`` means the run **never executed** (auth,
config, network), while ``result.status == "error"`` means it **ran and failed**.
Different fixes, different retry advice, different thing to tell the user — so a
node that collapses them into one ``except Exception`` throws away the only
information that decides what to do next.

Shared here rather than per node so CodeScope's local runner, CloudDispatcher's
cloud runtime, and the test fakes that stand in for both speak one vocabulary.
"""

from __future__ import annotations


class AgentError(RuntimeError):
    """Base for both failure modes."""


class AgentStartupError(AgentError):
    """The run never started. Fix the environment, then retry.

    ``retryable`` and ``retry_after`` carry the backend's own advice when there is
    any; honor both before falling back to backoff, since blind retries of a cloud
    launch can produce duplicate runs — and a duplicate run means a duplicate
    pull request.
    """

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = bool(retryable)
        self.retry_after = float(retry_after) if retry_after else None


class AgentRunError(AgentError):
    """The run executed and failed. The transcript is what to look at."""
