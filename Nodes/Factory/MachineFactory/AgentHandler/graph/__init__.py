"""The LangGraph work graphs an AgentHandler run executes.

Two specs live in ``wire.graph``. ``work`` is the straight line startup ->
pathfinder -> workhorse -> git -> teardown. ``massive`` is startup ->
orchestrator -> dispatcher -> ralph (loop) -> test -> teardown. Every node can
short-circuit to teardown so the run always publishes an outcome. Startup
clones the target repo; teardown pushes a branch and opens a pull request when
``auto_pr`` is set.

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
