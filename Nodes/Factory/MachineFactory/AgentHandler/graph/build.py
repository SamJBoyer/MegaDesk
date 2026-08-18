"""Compile the work graph from the shared topology.

The nodes and edges come from ``wire.graph.WORK_GRAPH`` rather than being
written out here, so the shape the visualizer draws is the shape that runs. If
someone adds a node to the spec without adding a function for it, this raises at
build time instead of drawing a node that never lights up.

The shape is the straight line from the design, with one addition: every edge
into a non-terminal node is conditional on ``state["error"]`` being empty. That
keeps the happy path linear while guaranteeing teardown runs, which is what
publishes FINISHED and deletes the handshake hash.
"""

from __future__ import annotations

from functools import partial
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph
from megadesk_contracts.wire import graph as wire

from AgentHandler.graph import nodes as node_impls
from AgentHandler.graph.state import RunContext, WorkState

NODE_FUNCTIONS: dict[str, Callable[..., WorkState]] = {
    "startup_node": node_impls.startup_node,
    "pathfinder_node": node_impls.pathfinder_node,
    "workhorse_node": node_impls.workhorse_node,
    "git_node": node_impls.git_node,
    "teardown_node": node_impls.teardown_node,
}


def _route_on_error(ok: str, terminal: str) -> Callable[[WorkState], str]:
    def route(state: WorkState) -> str:
        return terminal if state.get("error") else ok

    return route


def build_work_graph(
    context: RunContext,
    *,
    spec: wire.GraphSpec = wire.WORK_GRAPH,
) -> Any:
    """Compile ``spec`` into a runnable graph bound to one run's context."""
    missing = [name for name in spec.node_names() if name not in NODE_FUNCTIONS]
    if missing:
        raise ValueError(f"{spec.name} has no implementation for: {', '.join(missing)}")
    if not spec.nodes:
        raise ValueError(f"{spec.name} has no nodes")

    entry = spec.nodes[0].name
    terminal = spec.nodes[-1].name

    builder = StateGraph(WorkState)
    for node in spec.nodes:
        builder.add_node(node.name, partial(NODE_FUNCTIONS[node.name], context))

    builder.add_edge(START, entry)
    for source, target in spec.edges:
        if target == terminal:
            builder.add_edge(source, target)
        else:
            builder.add_conditional_edges(
                source,
                _route_on_error(target, terminal),
                {target: target, terminal: terminal},
            )
    builder.add_edge(terminal, END)

    return builder.compile()


def run_work_graph(
    context: RunContext,
    *,
    spec: wire.GraphSpec = wire.WORK_GRAPH,
) -> WorkState:
    """Build, announce and run the graph. Returns the final state."""
    compiled = build_work_graph(context, spec=spec)
    context.reporter.start_run()
    context.audit.event("graph-start", f"{spec.name}: {' -> '.join(spec.node_names())}")
    final: WorkState = compiled.invoke({"guid": context.guid})
    context.audit.event(
        "graph-done",
        f"status={final.get('status', 'unknown')} exit={final.get('exit_code', 1)}",
    )
    return final
