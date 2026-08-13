"""The OpenAI Realtime transport: microphone in, speaker out, events across.

Everything vendor-specific is in this file, behind
``megadesk_contracts.realtime.RealtimeTransport``. That boundary is what lets the
session logic — the tool router and the answer relay, which is where the design
actually lives — be tested with no socket, no API key, and no audio device.

Two things are worth knowing before changing anything here:

* **Server VAD does the hard part.** Turn-taking, endpointing and barge-in are the
  model's job, configured once in ``session.update``. There is no VAD code here,
  and there should not be.
* **The API renamed its audio and transcript events.** Both spellings are accepted
  below (``response.output_audio.delta`` and the older ``response.audio.delta``),
  because a rename should degrade to a missing feature rather than a silent mute.

``sounddevice`` and ``websockets`` are imported lazily, so importing this module
costs nothing and needs no PortAudio.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import queue
import threading
from typing import Any, Iterator, Optional

from megadesk_contracts.realtime import (
    EVENT_ASSISTANT_TEXT,
    EVENT_ERROR,
    EVENT_STATE,
    EVENT_TOOL_CALL,
    EVENT_TRANSCRIPT_FINAL,
    EVENT_TRANSCRIPT_PARTIAL,
    RealtimeEvent,
)
from megadesk_contracts.wire import voice as voice_wire

from VoiceDeckManager.tools import INSTRUCTIONS, tool_schemas

log = logging.getLogger("voice_deck.realtime")

REALTIME_URL = "wss://api.openai.com/v1/realtime"
DEFAULT_MODEL = "gpt-realtime"
DEFAULT_VOICE = "marin"
DEFAULT_TRANSCRIPTION_MODEL = "gpt-4o-mini-transcribe"

SAMPLE_RATE = 24000
CHANNELS = 1
# 20ms of 16-bit mono at 24kHz. Small enough that barge-in feels immediate,
# large enough not to spend the whole loop on socket framing.
BLOCK_FRAMES = 480

# Server events, old and new spellings.
_AUDIO_DELTA = ("response.output_audio.delta", "response.audio.delta")
_ASSISTANT_TRANSCRIPT_DONE = (
    "response.output_audio_transcript.done",
    "response.audio_transcript.done",
)
_INPUT_TRANSCRIPT_DELTA = "conversation.item.input_audio_transcription.delta"
_INPUT_TRANSCRIPT_DONE = "conversation.item.input_audio_transcription.completed"
_TOOL_ARGS_DONE = "response.function_call_arguments.done"
_OUTPUT_ITEM = ("response.output_item.added", "response.output_item.done")
_SPEECH_STARTED = "input_audio_buffer.speech_started"
_SPEECH_STOPPED = "input_audio_buffer.speech_stopped"


class RealtimeUnavailable(RuntimeError):
    """A dependency or credential the transport cannot work without is missing."""


class OpenAIRealtime:
    """A speech-to-speech session over a single websocket."""

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        model: str = "",
        voice: str = "",
        transcription_model: str = "",
        instructions: str = INSTRUCTIONS,
        audio: bool = True,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.environ.get("OPENAI_API_KEY")
        self.model = (model or os.environ.get("VOICE_MODEL") or DEFAULT_MODEL).strip()
        self.voice = (voice or os.environ.get("VOICE_NAME") or DEFAULT_VOICE).strip()
        self.transcription_model = (
            transcription_model
            or os.environ.get("VOICE_TRANSCRIBE_MODEL")
            or DEFAULT_TRANSCRIPTION_MODEL
        ).strip()
        self.instructions = instructions
        self.audio_enabled = bool(audio)

        self._socket: Any = None
        self._muted = False
        self._closing = threading.Event()
        self._playback: queue.Queue[bytes] = queue.Queue()
        self._threads: list[threading.Thread] = []
        # The capture thread sends audio while the session thread sends tool
        # results and injections. One socket, three writers, so one lock.
        self._send_lock = threading.Lock()
        # Tool names by call id, because the arguments-done event does not
        # reliably carry the function name — only the item that announced it does.
        self._call_names: dict[str, str] = {}

    # --- lifecycle ---

    def connect(self) -> None:
        if not self.api_key:
            raise RealtimeUnavailable("OPENAI_API_KEY is not set")
        try:
            from websockets.sync.client import connect as ws_connect
        except ImportError as exc:
            raise RealtimeUnavailable(
                "the 'websockets' package is required for voice"
            ) from exc

        url = f"{REALTIME_URL}?model={self.model}"
        log.info("Connecting to the realtime API model=%s", self.model)
        self._socket = ws_connect(
            url,
            additional_headers={"Authorization": f"Bearer {self.api_key}"},
            max_size=None,
            open_timeout=20,
        )
        self._closing.clear()
        try:
            self._send(self._session_update())
            if self.audio_enabled:
                self._start_audio()
        except Exception:
            # A socket that opened but has no microphone behind it is a billable
            # connection nobody is listening to.
            self.close()
            raise

    def close(self) -> None:
        self._closing.set()
        socket, self._socket = self._socket, None
        if socket is not None:
            try:
                socket.close()
            except Exception:  # noqa: BLE001
                log.debug("Socket close failed", exc_info=True)
        for thread in self._threads:
            thread.join(timeout=2.0)
        self._threads.clear()
        self._drain_playback()

    def set_muted(self, muted: bool) -> None:
        """Stop feeding the microphone. The socket stays open."""
        self._muted = bool(muted)
        log.info("Microphone %s", "muted" if self._muted else "live")

    # --- configuration ---

    def _session_update(self) -> dict:
        return {
            "type": "session.update",
            "session": {
                "type": "realtime",
                "output_modalities": ["audio"],
                "instructions": self.instructions,
                "tools": tool_schemas(),
                "audio": {
                    "input": {
                        "format": {"type": "audio/pcm", "rate": SAMPLE_RATE},
                        "transcription": {"model": self.transcription_model},
                        # interrupt_response is what makes talking over the model
                        # stop it, rather than leaving two voices going at once.
                        "turn_detection": {
                            "type": "server_vad",
                            "threshold": 0.5,
                            "prefix_padding_ms": 300,
                            "silence_duration_ms": 500,
                            "create_response": True,
                            "interrupt_response": True,
                        },
                    },
                    "output": {
                        "format": {"type": "audio/pcm", "rate": SAMPLE_RATE},
                        "voice": self.voice,
                    },
                },
            },
        }

    # --- sending ---

    def _send(self, event: dict) -> None:
        socket = self._socket
        if socket is None:
            raise RealtimeUnavailable("the realtime socket is not open")
        with self._send_lock:
            socket.send(json.dumps(event))

    def send_tool_result(self, call_id: str, payload: dict[str, Any]) -> None:
        self._send(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps(payload),
                },
            }
        )
        self._send({"type": "response.create"})

    def inject_assistant_text(self, text: str) -> None:
        """Hand the model text and have it speak it.

        The text goes in as a *user* item rather than an assistant one: an
        assistant item is only history, and history is not spoken. A user item
        plus ``response.create`` is what actually produces audio.
        """
        self._send(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": text}],
                },
            }
        )
        self._send(
            {
                "type": "response.create",
                "response": {
                    "instructions": (
                        "Relay the bracketed codebase answer to the user in one or "
                        "two spoken sentences. Do not read the marker aloud."
                    )
                },
            }
        )

    # --- receiving ---

    def events(self) -> Iterator[RealtimeEvent]:
        """Yield normalized events until the socket closes."""
        socket = self._socket
        if socket is None:
            return
        try:
            for message in socket:
                if self._closing.is_set():
                    return
                try:
                    payload = json.loads(message)
                except (TypeError, json.JSONDecodeError):
                    continue
                for event in self._normalize(payload):
                    yield event
        except Exception:  # noqa: BLE001
            # A socket closed by ``close()`` raises here; that is a shutdown, not
            # a failure the session should report as one.
            if not self._closing.is_set():
                raise

    def _normalize(self, payload: dict) -> list[RealtimeEvent]:
        kind = str(payload.get("type") or "")

        if kind in _AUDIO_DELTA:
            self._play(payload.get("delta") or "")
            return []
        if kind == _INPUT_TRANSCRIPT_DELTA:
            return [
                RealtimeEvent(
                    kind=EVENT_TRANSCRIPT_PARTIAL, text=str(payload.get("delta") or "")
                )
            ]
        if kind == _INPUT_TRANSCRIPT_DONE:
            return [
                RealtimeEvent(
                    kind=EVENT_TRANSCRIPT_FINAL, text=str(payload.get("transcript") or "")
                )
            ]
        if kind in _ASSISTANT_TRANSCRIPT_DONE:
            return [
                RealtimeEvent(
                    kind=EVENT_ASSISTANT_TEXT, text=str(payload.get("transcript") or "")
                )
            ]
        if kind in _OUTPUT_ITEM:
            self._remember_call(payload.get("item"))
            return []
        if kind == _TOOL_ARGS_DONE:
            call_id = str(payload.get("call_id") or "")
            name = str(payload.get("name") or "") or self._call_names.pop(call_id, "")
            return [
                RealtimeEvent(
                    kind=EVENT_TOOL_CALL,
                    name=name,
                    arguments=_parse_arguments(payload.get("arguments")),
                    call_id=call_id,
                )
            ]
        if kind == _SPEECH_STARTED:
            # Barge-in: whatever is queued for the speaker is already stale.
            self._drain_playback()
            return [RealtimeEvent(kind=EVENT_STATE, text=voice_wire.STATE_LISTENING)]
        if kind == _SPEECH_STOPPED:
            return [RealtimeEvent(kind=EVENT_STATE, text=voice_wire.STATE_THINKING)]
        if kind == "error":
            detail = payload.get("error") or {}
            message = detail.get("message") if isinstance(detail, dict) else str(detail)
            return [RealtimeEvent(kind=EVENT_ERROR, text=str(message or "realtime error"))]

        log.debug("Unhandled realtime event %s", kind)
        return []

    def _remember_call(self, item: Any) -> None:
        if not isinstance(item, dict) or item.get("type") != "function_call":
            return
        call_id = str(item.get("call_id") or "")
        name = str(item.get("name") or "")
        if call_id and name:
            self._call_names[call_id] = name

    # --- audio ---

    def _start_audio(self) -> None:
        try:
            import sounddevice  # noqa: F401 - probing for PortAudio
        except Exception as exc:  # noqa: BLE001 - a missing PortAudio raises OSError
            raise RealtimeUnavailable(
                f"sounddevice/PortAudio is unavailable: {exc}"
            ) from exc

        self._threads = [
            threading.Thread(target=self._capture_loop, daemon=True),
            threading.Thread(target=self._playback_loop, daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    def _capture_loop(self) -> None:
        import sounddevice as sd

        try:
            with sd.RawInputStream(
                samplerate=SAMPLE_RATE,
                blocksize=BLOCK_FRAMES,
                channels=CHANNELS,
                dtype="int16",
            ) as stream:
                while not self._closing.is_set():
                    block, _overflowed = stream.read(BLOCK_FRAMES)
                    if self._muted or self._socket is None:
                        continue
                    self._send(
                        {
                            "type": "input_audio_buffer.append",
                            "audio": base64.b64encode(bytes(block)).decode("ascii"),
                        }
                    )
        except Exception:  # noqa: BLE001 - losing the mic must not kill the BE
            if not self._closing.is_set():
                log.warning("Microphone capture stopped", exc_info=True)

    def _playback_loop(self) -> None:
        import sounddevice as sd

        try:
            with sd.RawOutputStream(
                samplerate=SAMPLE_RATE,
                blocksize=BLOCK_FRAMES,
                channels=CHANNELS,
                dtype="int16",
            ) as stream:
                while not self._closing.is_set():
                    try:
                        chunk = self._playback.get(timeout=0.2)
                    except queue.Empty:
                        continue
                    stream.write(chunk)
        except Exception:  # noqa: BLE001
            if not self._closing.is_set():
                log.warning("Speaker playback stopped", exc_info=True)

    def _play(self, b64: str) -> None:
        if not b64:
            return
        try:
            self._playback.put_nowait(base64.b64decode(b64))
        except Exception:  # noqa: BLE001 - a dropped frame is better than a stall
            log.debug("Dropped an audio frame")

    def _drain_playback(self) -> None:
        while True:
            try:
                self._playback.get_nowait()
            except queue.Empty:
                return


def _parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
