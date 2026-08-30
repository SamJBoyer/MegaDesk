"""What flows between work-graph nodes, and what sits beside it.

``WorkState`` is the part LangGraph merges between nodes: strings and numbers
that describe the run. ``RunContext`` is everything a node needs but must not
serialize — the Redis client, the audit log, the API key — held once for the
whole run and handed to the node closures when the graph is built.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, TypedDict

from megadesk_contracts.wire.factory import DEFAULT_STARTING_REF

from AgentHandler.repo_clone import SandboxRepo


class WorkState(TypedDict, total=False):
    """Merged between nodes. Every value is plain data."""

    guid: str
    ticket_id: str
    ticket_name: str
    repo: str
    model: str
    instructions: str
    pictures: list[str]
    auto_pr: bool
    pr_url: str

    # Set by any node that fails. Its presence is what routes to teardown.
    error: str
    failed_node: str

    pathfinder_report: str
    work_report: str
    diff_summary: str
    commit_sha: str
    commit_message: str

    # Massive-project graph. ``kanban`` is a list of plain card dicts.
    graph: str
    orchestrator_plan: str
    kanban: list[dict[str, Any]]
    ralph_report: str
    test_report: str

    status: str
    exit_code: int


@dataclass
class RunContext:
    """Live handles shared by every node in one run."""

    guid: str
    redis: Any
    audit: Any
    reporter: Any
    api_key: str
    workspace: str
    ticket: str = ""
    repo: str = ""
    repo_url: str = ""
    starting_ref: str = DEFAULT_STARTING_REF
    auto_pr: bool = True
    env_ticket_id: str = ""
    default_model: str = ""

    # Injected so tests can drive the graph without a real clone.
    repo_factory: Callable[..., Any] = field(default=SandboxRepo)
    _repo: Any = field(default=None, init=False, repr=False)

    def sandbox_repo(self) -> Any:
        if self._repo is None:
            self._repo = self.repo_factory(
                self.workspace,
                repo_url=self.repo_url,
                ticket=self.ticket,
                starting_ref=self.starting_ref,
                auto_pr=self.auto_pr,
            )
        return self._repo

    def prepare_git(self) -> None:
        self.sandbox_repo().prepare()

    def restore_git(self) -> None:
        self.sandbox_repo().restore()

    def publish_branch(self) -> str:
        return str(self.sandbox_repo().publish_branch() or "")
