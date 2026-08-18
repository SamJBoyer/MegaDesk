"""AgentHandler: one-shot AGENTHANDLER hash -> work graph on a mounted worktree.

MachineFactory's harness, and the reason a local run is worth having: everything
between the order and the commit is ours to change. A cloud run has no equivalent
seam — Cursor owns that VM — which is the one asymmetry the two Factory nodes
cannot design away.

A run is now a LangGraph: startup, pathfinder, workhorse, git, teardown. The
Cursor SDK is still driven from ``handler.run_agent``; the graph is the plot.
"""

from __future__ import annotations

__version__ = "0.1.0"

from megadesk_contracts.wire.machine import agent_handler_key

from AgentHandler.handler import AgentHandler, connect_redis, main

__all__ = [
    "AgentHandler",
    "agent_handler_key",
    "connect_redis",
    "main",
]
