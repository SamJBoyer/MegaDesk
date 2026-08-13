# VoiceDeck

Talk to your codebase. VoiceDeck is a transport, not a brain: it holds a
speech-to-speech session with the OpenAI Realtime API and routes the model's tool
calls to the nodes that actually do the work — CodeScope for questions,
CloudDispatcher for documentation agents.

## Halves

| Half | What it does |
|------|--------------|
| FE (`voice_deck_frontend/app.py`) | Push-to-talk, mute, auto-dispatch toggle, repo target, state dot, rolling transcript |
| BE (`VoiceDeckManager/`) | Microphone and speaker, realtime websocket, tool router, answer relay |

**Audio never crosses Redis.** Only control messages (`VOICE:CONTROL`) and text
(`VOICE:EVENT`: partial transcript, final transcript, answer, state, dispatch,
target) do. This is the same rule that keeps log bodies off the Supervisor
streams, and it means a stalled canvas cannot stutter the conversation.

The BE does not open the microphone at boot. It idles until it sees
`VOICE:CONTROL start`, because a hot mic that switched on when a node launched is
not something anyone asked for.

## The timing problem, and the fix

A Cursor agent takes five to sixty seconds to answer. A realtime voice loop
expects a tool result in well under a second. Blocking the tool call stalls the
conversation and the model goes quiet, which sounds exactly like a crash.

So the answer is decoupled from the tool result:

1. `ask_codebase` publishes `CODEQ:ASK` and returns `status: searching` immediately.
2. The model is instructed to say one short thing and then wait.
3. CodeScope streams the answer back sentence by sentence on `CODEQ:ANSWER`.
4. Each sentence is injected as a new conversation item plus a `response.create`,
   so the model speaks it as it arrives.

Answers are pumped on their own thread, because the transport's event iterator
blocks on the socket — an answer that arrived during that block would otherwise
wait for the user to speak again.

## Tools the model can call

| Tool | Effect |
|------|--------|
| `ask_codebase(question)` | `CODEQ:ASK` to CodeScope; returns `searching` |
| `dispatch_doc_agent(title, instructions, target)` | A **draft** in CloudDispatcher, unless auto-dispatch is on |
| `set_repo(repo)` | Switch which loaded repo questions are about |
| `end_session()` | Close the socket |

**Voice cannot open a pull request by itself.** `dispatch_doc_agent` writes a
`CLOUDDRAFT:<order_id>` hash that CloudDispatcher shows as a row with a button;
nothing runs until someone presses it. Flipping the `auto` checkbox in the FE
changes that, deliberately and visibly.

## Turn-taking

Server VAD does it. Endpointing, interruption and barge-in are configured once in
`session.update` (`turn_detection.type: server_vad`, `interrupt_response: true`)
and there is no VAD code in this node. On `input_audio_buffer.speech_started` the
BE drops whatever is queued for the speaker, so talking over the model stops it
rather than leaving two voices running.

## Requirements

- `OPENAI_API_KEY`
- `pip install -e Nodes/VoiceDeck[canvas,audio]` — `sounddevice` needs PortAudio,
  which is why audio is an extra: the FE and the wire tests import without it
- A repo loaded in CodeScope; there is nothing to ask about otherwise
- Roughly $0.05–0.10 per minute of audio

`python -m VoiceDeckManager devices` lists audio devices, which is the first
thing to check when voice is silent.

## Testing

`FakeRealtime` in `megadesk_contracts.testing` replaces the socket with a script,
so tests exercise the real tool router, the real Redis payloads and the real
injection path with no microphone and no API key. The transport boundary is
`megadesk_contracts.realtime.RealtimeTransport` — five fields and six verbs, so
the vendor's schema churn stays in one file.
