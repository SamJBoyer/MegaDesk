"""The normalized shape of a speech-to-speech transport.

VoiceDeck's logic — the tool router, the Redis events, the out-of-band answer
injection — is worth testing without a microphone or a socket, so it is written
against this small surface instead of against OpenAI's event schema. The vendor
schema is wide, versioned, and renames events between releases; everything below
is five fields and six verbs.

Kept in contracts rather than in the node because both sides need it: the real
transport produces these events, and ``FakeRealtime`` in
``megadesk_contracts.testing`` produces the identical ones from a script.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Protocol, runtime_checkable

EVENT_TRANSCRIPT_PARTIAL = "transcript_partial"
EVENT_TRANSCRIPT_FINAL = "transcript_final"
EVENT_ASSISTANT_TEXT = "assistant_text"
EVENT_TOOL_CALL = "tool_call"
EVENT_STATE = "state"
EVENT_ERROR = "error"

EVENT_KINDS = frozenset(
    {
        EVENT_TRANSCRIPT_PARTIAL,
        EVENT_TRANSCRIPT_FINAL,
        EVENT_ASSISTANT_TEXT,
        EVENT_TOOL_CALL,
        EVENT_STATE,
        EVENT_ERROR,
    }
)


@dataclass
class RealtimeEvent:
    """One thing the model did, with the vendor's schema already stripped off."""

    kind: str
    text: str = ""
    name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    call_id: str = ""


@runtime_checkable
class RealtimeTransport(Protocol):
    """What VoiceDeck needs from a voice connection.

    Audio never appears here: capture and playback are the transport's business
    and stay inside it, which is also why no PCM ever reaches Redis.
    """

    def connect(self) -> None: ...

    def close(self) -> None: ...

    def events(self) -> Iterator[RealtimeEvent]: ...

    def send_tool_result(self, call_id: str, payload: dict[str, Any]) -> None: ...

    def inject_assistant_text(self, text: str) -> None: ...

    def set_muted(self, muted: bool) -> None: ...
