"""VoiceDeck wire format: text and control only, never audio.

(STREAM, db0) VOICE:CONTROL   FE -> BE
  - action, value

(STREAM, db0) VOICE:EVENT     BE -> FE
  - kind, text, session_id

Audio never crosses Redis. Microphone frames and spoken output stay inside the
BE process for the same reason log bodies stay off the Supervisor streams: the
stream is a control plane, and 24kHz PCM would swamp it. The FE therefore shows
transcripts and state, and cannot ever be the thing that stalls the audio loop.
"""

from __future__ import annotations

from typing import Any, Mapping

from megadesk_contracts.wire._fields import (
    one_of,
    require,
    stripped,
    text_field,
)

CONTROL_STREAM = "VOICE:CONTROL"
EVENT_STREAM = "VOICE:EVENT"

# FE -> BE. ``target`` carries a repo name.
ACTION_START = "start"
ACTION_STOP = "stop"
ACTION_MUTE = "mute"
ACTION_UNMUTE = "unmute"
ACTION_TARGET = "target"
CONTROL_ACTIONS = frozenset(
    {
        ACTION_START,
        ACTION_STOP,
        ACTION_MUTE,
        ACTION_UNMUTE,
        ACTION_TARGET,
    }
)

# BE -> FE. ``partial`` is in-flight recognition, ``final`` a settled user turn,
# ``answer`` what the assistant said, ``state`` a lifecycle change.
KIND_PARTIAL = "partial"
KIND_FINAL = "final"
KIND_ANSWER = "answer"
KIND_STATE = "state"
KIND_ERROR = "error"
# ``dispatch`` announces a cloud order; ``target`` reports the repo the model is
# now asking about, which it can change on its own via a tool call.
KIND_DISPATCH = "dispatch"
KIND_TARGET = "target"
EVENT_KINDS = frozenset(
    {
        KIND_PARTIAL,
        KIND_FINAL,
        KIND_ANSWER,
        KIND_STATE,
        KIND_ERROR,
        KIND_DISPATCH,
        KIND_TARGET,
    }
)

STATE_OFF = "off"
STATE_CONNECTING = "connecting"
STATE_LISTENING = "listening"
STATE_THINKING = "thinking"
STATE_SPEAKING = "speaking"
STATE_MUTED = "muted"
STATE_ERROR = "error"
VOICE_STATES = frozenset(
    {
        STATE_OFF,
        STATE_CONNECTING,
        STATE_LISTENING,
        STATE_THINKING,
        STATE_SPEAKING,
        STATE_MUTED,
        STATE_ERROR,
    }
)


def control_fields(*, action: str, value: str = "") -> dict[str, str]:
    return {
        "action": one_of(
            "VOICE:CONTROL", "action", stripped(action), CONTROL_ACTIONS
        ),
        "value": text_field(value),
    }


def parse_control(fields: Mapping[str, Any]) -> dict[str, str]:
    parsed = {
        "action": stripped(fields.get("action")),
        "value": text_field(fields.get("value")),
    }
    one_of("VOICE:CONTROL", "action", parsed["action"], CONTROL_ACTIONS)
    return parsed


def event_fields(*, kind: str, text: str, session_id: str = "") -> dict[str, str]:
    fields = {
        "kind": one_of("VOICE:EVENT", "kind", stripped(kind), EVENT_KINDS),
        "text": text_field(text),
        "session_id": stripped(session_id),
    }
    require("VOICE:EVENT", fields, ("text",))
    if fields["kind"] == KIND_STATE:
        one_of("VOICE:EVENT", "text", stripped(fields["text"]), VOICE_STATES)
    return fields


def parse_event(fields: Mapping[str, Any]) -> dict[str, str]:
    parsed = {
        "kind": stripped(fields.get("kind")),
        "text": text_field(fields.get("text")),
        "session_id": stripped(fields.get("session_id")),
    }
    one_of("VOICE:EVENT", "kind", parsed["kind"], EVENT_KINDS)
    require("VOICE:EVENT", parsed, ("text",))
    return parsed
