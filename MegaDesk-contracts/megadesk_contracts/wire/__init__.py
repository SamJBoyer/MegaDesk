"""Canonical Redis payload shapes for MegaDesk nodes.

One definition per stream, imported by both halves of every node that touches
it. MergeManager and MissionControl each ship a copy of a shared
``redis_packets`` module, and ``tests/test_wire_contract.py`` exists to catch the
two copies drifting apart; new streams live here instead so there is only ever
one copy to keep honest.

Submodules are exported rather than flattened, because several of them
legitimately define the same names (``DEFAULT_MODEL``, ``parse_*``)::

    from megadesk_contracts import wire

    wire.code_scope.ask_fields(...)
    wire.cloud.cloudorder_fields(...)
    wire.voice.event_fields(...)
"""

from megadesk_contracts.wire import cloud, code_scope, voice
from megadesk_contracts.wire._fields import BOOL_FALSE, BOOL_TRUE, bool_field, is_true

__all__ = [
    "BOOL_FALSE",
    "BOOL_TRUE",
    "bool_field",
    "cloud",
    "code_scope",
    "is_true",
    "voice",
]
