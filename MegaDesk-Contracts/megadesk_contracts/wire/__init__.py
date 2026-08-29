"""Canonical Redis payload shapes for MegaDesk nodes.

One definition per stream, imported by both halves of every node that touches it.
Nothing here is a copy of anything: a node that ships its own ``redis_packets``
is a node with a second opinion about the wire, and there is no way to tell which
opinion is current until something silently stops matching.

Submodules are exported rather than flattened, because several of them
legitimately define the same names (``DEFAULT_MODEL``, ``parse_*``)::

    from megadesk_contracts import wire

    wire.machine.workorder_fields(...)
    wire.cloud.cloudorder_fields(...)
    wire.code_scope.ask_fields(...)
    wire.voice.event_fields(...)
    wire.graph.graph_run_fields(...)

``wire.factory`` holds what the two Factory families share — the run statuses —
so ``wire.machine`` and ``wire.cloud`` can stay separate without inventing two
words for the same outcome. ``wire.graph`` reuses those same statuses one level
down, for the nodes inside a single run.
"""

from megadesk_contracts.wire import cloud, code_scope, factory, graph, machine, signal, voice
from megadesk_contracts.wire._fields import BOOL_FALSE, BOOL_TRUE, bool_field, is_true

__all__ = [
    "BOOL_FALSE",
    "BOOL_TRUE",
    "bool_field",
    "cloud",
    "code_scope",
    "factory",
    "graph",
    "is_true",
    "machine",
    "signal",
    "voice",
]
