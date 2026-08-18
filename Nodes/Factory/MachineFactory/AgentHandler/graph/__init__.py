"""The LangGraph work graph an AgentHandler run executes.

startup -> pathfinder -> workhorse -> git -> teardown, with every node able to
short-circuit to teardown so the run always publishes an outcome. Startup
rewrites gitdir pointers for the sandbox mounts; teardown writes the host
pointers back so the worktree is mergeable.

LangGraph is used for orchestration only. The agent nodes drive Cursor through
``AgentHandler.handler.run_agent`` exactly as the single-shot handler did, so
there is no chat model, no provider config and no second API key here.
"""

from __future__ import annotations

from AgentHandler.graph.build import (
    NODE_FUNCTIONS,
    build_work_graph,
    run_work_graph,
)
from AgentHandler.graph.reporter import GraphReporter
from AgentHandler.graph.state import RunContext, WorkState

__all__ = [
    "NODE_FUNCTIONS",
    "GraphReporter",
    "RunContext",
    "WorkState",
    "build_work_graph",
    "run_work_graph",
]
