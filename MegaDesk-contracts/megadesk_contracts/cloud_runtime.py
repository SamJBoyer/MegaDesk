"""The normalized shape of a place that runs an agent for you.

CloudDispatcher's logic — the CLOUDORDER consumer group, the run registry on
db 1, the retry rules, the CLOUDFINISHED payloads — is worth testing without
spending money on a Cursor VM or opening a pull request on a real repository, so
it is written against this surface instead of against ``cursor_sdk`` directly.

Three verbs and two shapes. ``launch`` either returns an id or raises
``AgentStartupError``; that distinction is the whole point, because a run that
never started is worth retrying and a run that started and failed is not.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class CloudLaunch:
    """What a runtime hands back once an agent exists somewhere."""

    agent_id: str
    run_id: str = ""


@dataclass
class CloudStatus:
    """Where a launched run has got to, and what it produced."""

    status: str
    pr_url: str = ""
    detail: str = ""


@runtime_checkable
class CloudRuntime(Protocol):
    """What CloudDispatcher needs from whoever actually runs the agent."""

    def launch(
        self,
        *,
        repo_url: str,
        instructions: str,
        title: str,
        model: str,
        auto_pr: bool = True,
        ref: str = "",
    ) -> CloudLaunch: ...

    def poll(self, agent_id: str) -> CloudStatus: ...

    def cancel(self, agent_id: str) -> None: ...
