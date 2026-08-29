"""Tool set the notepad exposes to the voice agent.

Canonical names and schemas live in ``megadesk_contracts.wire.notepad`` so
VoiceDeck and this node cannot drift. This module is the node's public surface.
"""

from megadesk_contracts.wire.notepad import (
    TOOL_ADD_TEXT,
    TOOL_NEW_DOCUMENT,
    TOOL_SWITCH_DOCUMENT,
    tool_schemas,
)

__all__ = [
    "TOOL_ADD_TEXT",
    "TOOL_NEW_DOCUMENT",
    "TOOL_SWITCH_DOCUMENT",
    "tool_schemas",
]
