"""What flows between work-graph nodes, and what sits beside it.

``WorkState`` is the part LangGraph merges between nodes: strings and numbers
that describe the run. ``RunContext`` is everything a node needs but must not
serialize — the Redis client, the audit log, the API key — held once for the
whole run and handed to the node closures when the graph is built.

The split matters because LangGraph copies state around. A live socket in there
would either break or, worse, quietly work until something tried to checkpoint.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, TypedDict

from AgentHandler.worktree_bind import WorktreeGitBind


class WorkState(TypedDict, total=False):
    """Merged between nodes. Every value is plain data."""

    guid: str
    ticket_id: str
    ticket_name: str
    repo: str
    model: str
    instructions: str
    wt: str
    agent_dir: str

    # Set by any node that fails. Its presence is what routes to teardown.
    error: str
    failed_node: str

    pathfinder_report: str
    work_report: str
    diff_summary: str
    commit_sha: str
    commit_message: str

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
    bare_mount: str = "/bare"
    ticket: str = ""
    repo: str = ""
    host_wt: str = ""
    host_agent_dir: str = ""
    env_ticket_id: str = ""
    default_model: str = ""

    # Injected so tests can drive the graph without a real Floor mount. In the
    # sandbox this is the real thing and the pointers genuinely need rewriting.
    git_bind_factory: Callable[..., Any] = field(default=WorktreeGitBind)

    def git_bind(self, ticket: str = "") -> Any:
        return self.git_bind_factory(
            self.workspace,
            bare_mount=self.bare_mount,
            ticket=ticket or self.ticket,
        )
