"""Work-graph wire format: how far along a sandbox run is, node by node.

(HASH, db0) GRAPHRUN:<guid>
  - guid, graph, spec, nodes, current, status, ticket_id, ticket_name, repo,
    started, updated, error

(STREAM, db0) GRAPHEVENT
  - guid, graph, node, status, detail, ts

``AGENTHANDLER:<guid>`` says whether a run is alive; this says where inside the
run it is. They are deliberately separate keys rather than more fields on one
hash: the factory owns AGENTHANDLER and reaps against it, while GRAPHRUN is
written only from inside the sandbox by the graph itself. Both are deleted at
teardown, so a missing GRAPHRUN still means "no run" and the monitor stays
truthful without reconciling anything.

The hash carries its own topology in ``spec``. A visualizer could import
``WORK_GRAPH`` and draw that instead, but then it would be drawing the graph it
was compiled against rather than the graph that actually ran, and the two would
diverge the first time anyone added a node. Publishing the shape with the run
costs one field and removes the question.

``GRAPHEVENT`` outlives the hash on purpose. The hash answers "what is happening
now" and is gone the moment the container exits; the stream answers "what
happened", which is the only question left once it has.

Node status reuses the run vocabulary from ``wire.factory`` rather than
inventing a second one. A node that never ran because an earlier node failed
stays ``queued`` — it was never cancelled, it was simply never reached, and
``queued`` already means exactly that.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from megadesk_contracts.wire._fields import (
    one_of,
    require,
    stripped,
    text_field,
)
from megadesk_contracts.wire.factory import (
    RUN_STATUSES,
    STATUS_CANCELLED,
    STATUS_ERROR,
    STATUS_FINISHED,
    STATUS_QUEUED,
    STATUS_RUNNING,
    TERMINAL_STATUSES,
    is_terminal,
)

GRAPHRUN_PREFIX = "GRAPHRUN:"
GRAPHEVENT_STREAM = "GRAPHEVENT"

# The stream is a timeline, not a log: capped so a long-lived bus cannot grow
# without bound, generous enough that a run's own events survive it.
GRAPHEVENT_MAXLEN = 4096

KIND_SCRIPT = "script"
KIND_AGENT = "agent"
NODE_KINDS = frozenset({KIND_SCRIPT, KIND_AGENT})

__all__ = [
    "GRAPHEVENT_MAXLEN",
    "GRAPHEVENT_STREAM",
    "GRAPHRUN_PREFIX",
    "KIND_AGENT",
    "KIND_SCRIPT",
    "NODE_KINDS",
    "RUN_STATUSES",
    "STATUS_CANCELLED",
    "STATUS_ERROR",
    "STATUS_FINISHED",
    "STATUS_QUEUED",
    "STATUS_RUNNING",
    "TERMINAL_STATUSES",
    "WORK_GRAPH",
    "GraphSpec",
    "GraphNodeSpec",
    "decode_nodes",
    "decode_spec",
    "encode_nodes",
    "encode_spec",
    "graph_event_fields",
    "graph_run_key",
    "graph_run_fields",
    "guid_from_graph_run_key",
    "initial_nodes",
    "is_terminal",
    "node_progress",
    "parse_graph_event",
    "parse_graph_run",
    "read_graph_events",
    "wire_timestamp",
]


def wire_timestamp() -> str:
    """One timestamp spelling for every writer, so the FE can sort on it."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def graph_run_key(guid: str) -> str:
    text = stripped(guid)
    if not text:
        raise ValueError("GRAPHRUN requires a guid")
    return f"{GRAPHRUN_PREFIX}{text}"


def guid_from_graph_run_key(key: str) -> str:
    if not key.startswith(GRAPHRUN_PREFIX):
        raise ValueError(f"Key {key!r} is not a {GRAPHRUN_PREFIX}* hash")
    guid = key[len(GRAPHRUN_PREFIX) :]
    if not guid:
        raise ValueError(f"Empty guid in key {key!r}")
    return guid


# --- topology --------------------------------------------------------------


@dataclass(frozen=True)
class GraphNodeSpec:
    """One node in a work graph.

    ``kind`` is what a visualizer draws differently and nothing else branches
    on: a script node is deterministic plumbing, an agent node spends money and
    takes minutes, and those deserve to look different at a glance.
    """

    name: str
    kind: str
    label: str
    description: str = ""


@dataclass(frozen=True)
class GraphSpec:
    name: str
    nodes: tuple[GraphNodeSpec, ...]
    edges: tuple[tuple[str, str], ...]

    def node_names(self) -> tuple[str, ...]:
        return tuple(node.name for node in self.nodes)

    def node(self, name: str) -> GraphNodeSpec:
        for candidate in self.nodes:
            if candidate.name == name:
                return candidate
        raise KeyError(f"{self.name} has no node {name!r}")

    def validate(self) -> "GraphSpec":
        names = self.node_names()
        if len(set(names)) != len(names):
            raise ValueError(f"{self.name} has duplicate node names: {names}")
        for source, target in self.edges:
            if source not in names:
                raise ValueError(f"{self.name} edge from unknown node {source!r}")
            if target not in names:
                raise ValueError(f"{self.name} edge to unknown node {target!r}")
        for node in self.nodes:
            one_of(self.name, "kind", node.kind, NODE_KINDS)
        return self


WORK_GRAPH = GraphSpec(
    name="work",
    nodes=(
        GraphNodeSpec(
            name="startup_node",
            kind=KIND_SCRIPT,
            label="startup",
            description="Read the run handshake, load the order, and bind git to /bare.",
        ),
        GraphNodeSpec(
            name="pathfinder_node",
            kind=KIND_AGENT,
            label="pathfinder",
            description="Survey the worktree and ready the environment.",
        ),
        GraphNodeSpec(
            name="workhorse_node",
            kind=KIND_AGENT,
            label="workhorse",
            description="Do the ticket work.",
        ),
        GraphNodeSpec(
            name="git_node",
            kind=KIND_AGENT,
            label="git",
            description="Read the diff and commit it with a real message.",
        ),
        GraphNodeSpec(
            name="teardown_node",
            kind=KIND_SCRIPT,
            label="teardown",
            description="Restore host gitdir pointers, publish the outcome, and stop.",
        ),
    ),
    edges=(
        ("startup_node", "pathfinder_node"),
        ("pathfinder_node", "workhorse_node"),
        ("workhorse_node", "git_node"),
        ("git_node", "teardown_node"),
    ),
).validate()


def encode_spec(spec: GraphSpec) -> str:
    return json.dumps(
        {
            "name": spec.name,
            "nodes": [
                {
                    "name": node.name,
                    "kind": node.kind,
                    "label": node.label,
                    "description": node.description,
                }
                for node in spec.nodes
            ],
            "edges": [list(edge) for edge in spec.edges],
        },
        separators=(",", ":"),
    )


def decode_spec(text: Any) -> GraphSpec:
    raw = stripped(text)
    if not raw:
        raise ValueError("GRAPHRUN spec is empty")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"GRAPHRUN spec is not JSON: {exc}") from exc
    if not isinstance(data, Mapping):
        raise ValueError("GRAPHRUN spec must be a JSON object")
    nodes = tuple(
        GraphNodeSpec(
            name=stripped(item.get("name")),
            kind=stripped(item.get("kind")) or KIND_SCRIPT,
            label=stripped(item.get("label")) or stripped(item.get("name")),
            description=text_field(item.get("description")),
        )
        for item in data.get("nodes") or ()
    )
    edges = tuple(
        (stripped(edge[0]), stripped(edge[1]))
        for edge in data.get("edges") or ()
        if len(edge) >= 2
    )
    return GraphSpec(
        name=stripped(data.get("name")) or "graph",
        nodes=nodes,
        edges=edges,
    ).validate()


# --- per-node progress -----------------------------------------------------


def node_progress(
    *,
    status: str = STATUS_QUEUED,
    started: str = "",
    ended: str = "",
    detail: str = "",
) -> dict[str, str]:
    value = stripped(status) or STATUS_QUEUED
    one_of("GRAPHRUN", "node status", value, RUN_STATUSES)
    return {
        "status": value,
        "started": stripped(started),
        "ended": stripped(ended),
        "detail": text_field(detail),
    }


def initial_nodes(spec: GraphSpec) -> dict[str, dict[str, str]]:
    """Every node queued, so the visualizer can draw the whole shape at once."""
    return {node.name: node_progress() for node in spec.nodes}


def encode_nodes(nodes: Mapping[str, Mapping[str, Any]]) -> str:
    return json.dumps(
        {
            str(name): node_progress(
                status=progress.get("status", STATUS_QUEUED),
                started=progress.get("started", ""),
                ended=progress.get("ended", ""),
                detail=progress.get("detail", ""),
            )
            for name, progress in nodes.items()
        },
        separators=(",", ":"),
    )


def decode_nodes(text: Any) -> dict[str, dict[str, str]]:
    raw = stripped(text)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"GRAPHRUN nodes is not JSON: {exc}") from exc
    if not isinstance(data, Mapping):
        raise ValueError("GRAPHRUN nodes must be a JSON object")
    return {
        str(name): node_progress(
            status=progress.get("status", STATUS_QUEUED),
            started=progress.get("started", ""),
            ended=progress.get("ended", ""),
            detail=progress.get("detail", ""),
        )
        for name, progress in data.items()
        if isinstance(progress, Mapping)
    }


# --- GRAPHRUN:<guid> -------------------------------------------------------


def graph_run_fields(
    *,
    guid: str,
    graph: str,
    spec: str,
    nodes: str,
    status: str,
    ticket_id: str = "",
    ticket_name: str = "",
    repo: str = "",
    current: str = "",
    started: str = "",
    updated: str = "",
    error: str = "",
) -> dict[str, str]:
    """Build the GRAPHRUN hash.

    ``spec`` and ``nodes`` arrive already encoded so that a caller updating one
    field does not have to rebuild the other; use ``encode_spec`` /
    ``encode_nodes``.
    """
    fields = {
        "guid": stripped(guid),
        "graph": stripped(graph),
        "spec": text_field(spec),
        "nodes": text_field(nodes),
        "current": stripped(current),
        "status": stripped(status),
        "ticket_id": stripped(ticket_id),
        "ticket_name": stripped(ticket_name),
        "repo": stripped(repo),
        "started": stripped(started),
        "updated": stripped(updated) or wire_timestamp(),
        "error": text_field(error),
    }
    require("GRAPHRUN", fields, ("guid", "graph", "spec", "nodes", "status"))
    one_of("GRAPHRUN", "status", fields["status"], RUN_STATUSES)
    return fields


def parse_graph_run(fields: Mapping[str, Any]) -> dict[str, Any]:
    parsed = {
        "guid": stripped(fields.get("guid")),
        "graph": stripped(fields.get("graph")),
        "spec": text_field(fields.get("spec")),
        "nodes": text_field(fields.get("nodes")),
        "current": stripped(fields.get("current")),
        "status": stripped(fields.get("status")),
        "ticket_id": stripped(fields.get("ticket_id")),
        "ticket_name": stripped(fields.get("ticket_name")),
        "repo": stripped(fields.get("repo")),
        "started": stripped(fields.get("started")),
        "updated": stripped(fields.get("updated")),
        "error": text_field(fields.get("error")),
    }
    require("GRAPHRUN", parsed, ("guid", "graph", "spec", "status"))
    return parsed


# --- GRAPHEVENT ------------------------------------------------------------


def graph_event_fields(
    *,
    guid: str,
    graph: str,
    node: str,
    status: str,
    detail: str = "",
    ts: str = "",
) -> dict[str, str]:
    fields = {
        "guid": stripped(guid),
        "graph": stripped(graph),
        "node": stripped(node),
        "status": stripped(status),
        "detail": text_field(detail),
        "ts": stripped(ts) or wire_timestamp(),
    }
    require("GRAPHEVENT", fields, ("guid", "graph", "node", "status"))
    one_of("GRAPHEVENT", "status", fields["status"], RUN_STATUSES)
    return fields


def parse_graph_event(fields: Mapping[str, Any]) -> dict[str, str]:
    parsed = {
        "guid": stripped(fields.get("guid")),
        "graph": stripped(fields.get("graph")),
        "node": stripped(fields.get("node")),
        "status": stripped(fields.get("status")),
        "detail": text_field(fields.get("detail")),
        "ts": stripped(fields.get("ts")),
    }
    require("GRAPHEVENT", parsed, ("guid", "graph", "node", "status"))
    return parsed


def read_graph_events(
    redis: Any,
    guid: str,
    *,
    count: int = 200,
) -> list[dict[str, str]]:
    """The timeline for one run, oldest first.

    One shared stream keeps the key count flat, so a reader filters by guid
    rather than knowing a key per run.
    """
    wanted = stripped(guid)
    rows: Iterable[Any] = redis.xrevrange(GRAPHEVENT_STREAM, count=count) or ()
    events: list[dict[str, str]] = []
    for _entry_id, fields in rows:
        try:
            event = parse_graph_event(fields)
        except ValueError:
            continue
        if event["guid"] == wanted:
            events.append(event)
    events.reverse()
    return events
