# Voice chain: CodeScope → VoiceDeck → CloudDispatcher

Three nodes, five streams, three hashes. Unlike the MissionControl pipeline, every
package here has exactly **one** definition, in
[`megadesk_contracts/wire/`](../megadesk_contracts/wire/) — `code_scope.py`, `voice.py`,
`cloud.py` — imported by both halves of every node. There is no per-node
`redis_packets.py` copy to drift.

Streams use the database `REDIS_URL` names (0 by default). The three hashes are pinned to
**DB 1**, because they have to outlive the stream traffic and the processes.

```mermaid
sequenceDiagram
    participant VDFE as voice_deck FE
    participant VD as voice_deck BE
    participant RT as OpenAI Realtime
    participant CS as code_scope BE
    participant CD as cloud_dispatcher BE

    VDFE->>VD: VOICE:CONTROL start
    VD->>VDFE: VOICE:EVENT state=listening
    RT->>VD: tool call ask_codebase
    VD->>CS: XADD CODEQ:ASK
    VD-->>RT: tool result status=searching
    CS->>VD: XADD CODEQ:ANSWER (per sentence)
    VD->>RT: conversation.item.create + response.create
    VD->>VDFE: VOICE:EVENT answer
    RT->>VD: tool call dispatch_doc_agent
    VD->>VD: HSET CLOUDDRAFT:<order_id> (DB 1)
    Note over CD: a click in the FE turns the draft into an order
    CD->>CD: HSET CLOUDRUN:bc-xxx status=running (DB 1)
    CD->>VDFE: XADD CLOUDFINISHED (agent_id, status, pr_url)
```

## CODEQ:ASK

Stream, DB 0. Consumer group `code_scope`.

| Field | Meaning |
|---|---|
| `session_id` | Which `CODESCOPE:SESSION:<id>` to ask against |
| `question_id` | Correlates every answer entry with its question |
| `repo` | Repo name, for display and routing |
| `question` | The question, self-contained |
| `mode` | `answer` or `propose_ticket` |

## CODEQ:ANSWER

Stream, DB 0. Read with plain `XREAD`, **not** a consumer group: the CodeScope FE and
VoiceDeck both read the same entries, and a group would let whichever asked first steal
them.

| Field | Meaning |
|---|---|
| `session_id`, `question_id`, `repo` | Echoed from the ask |
| `answer` | One sentence-ish chunk, published as the agent produces it |
| `final` | `"true"` on the last entry only, including when it is empty |
| `status` | `ok` or `error`; an error answer is always final |

## VOICE:CONTROL

Stream, DB 0. FE → BE, plain `XREAD` from the tail.

| Field | Values |
|---|---|
| `action` | `start`, `stop`, `mute`, `unmute`, `target`, `auto_dispatch` |
| `value` | Repo name for `target`, `"true"` / `"false"` for `auto_dispatch`, else `""` |

The BE reads from the stream's tail at boot, so a `start` published before it woke up is
ignored — a microphone that switches itself on from stream history is the worst failure
available to this node.

## VOICE:EVENT

Stream, DB 0. BE → FE.

| Field | Values |
|---|---|
| `kind` | `partial`, `final`, `answer`, `state`, `error`, `dispatch`, `target` |
| `text` | For `state`: one of `off`, `connecting`, `listening`, `thinking`, `speaking`, `muted`, `error` |
| `session_id` | The voice session that emitted it |

**Audio never appears on Redis.** Microphone frames and spoken output stay inside the BE,
for the same reason log line bodies stay off the Supervisor streams: the stream is a
control plane, and 24kHz PCM would swamp it.

## CLOUDORDER

Stream, DB 0. Consumer group `cloud_dispatcher`.

| Field | Meaning |
|---|---|
| `order_id` | Minted by whoever ordered; survives the round trip |
| `repo_url` | The whole input — a cloud agent clones from GitHub itself |
| `ref` | Optional base ref |
| `title` | PR title, under ten words |
| `instructions` | What to change; the agent has no other context |
| `model` | Model id, or `auto` |
| `auto_pr` | `"true"` opens a PR when the work lands |

## CLOUDFINISHED

Stream, DB 0.

| Field | Meaning |
|---|---|
| `agent_id` | Cursor's `bc-` id, empty **only** for `startup_error` |
| `order_id` | The order this settles |
| `status` | `finished`, `error`, `cancelled`, `startup_error` |
| `pr_url` | The pull request, when there is one |

`startup_error` means the run never started (fix auth or config, then retry); `error`
means it ran and failed (read the transcript). Collapsing the two loses the only
information that decides what to do next.

## Hashes (DB 1)

| Key | Fields | Owner |
|---|---|---|
| `CODESCOPE:SESSION:<id>` | `repo`, `clone_path`, `agent_id`, `model`, `status` | CodeScope FE writes, BE updates `agent_id` / `status` |
| `CLOUDRUN:<agent_id>` | `order_id`, `repo_url`, `title`, `status`, `pr_url`, `run_id` | CloudDispatcher BE |
| `CLOUDDRAFT:<order_id>` | exactly the `CLOUDORDER` field set | VoiceDeck writes, CloudDispatcher FE consumes |

`agent_id` on a session is what lets a restarted CodeScope BE `Agent.resume` instead of
starting cold. `CLOUDRUN` is written before its order is acked, so a crash in between
leaves a visible run rather than an order that silently launched nothing — and its stored
status is what makes `CLOUDFINISHED` fire exactly once per run.

A `CLOUDDRAFT` carries the order's fields verbatim so that approving it adds nothing of
its own. That is the safety rail: voice writes drafts, and only a click writes orders.

## Code references

- `MegaDesk-contracts/megadesk_contracts/wire/{code_scope,voice,cloud}.py` — the definitions
- `Nodes/CodeScope/CodeScopeManager/manager.py`, `Nodes/CodeScope/code_scope_frontend/app.py`
- `Nodes/VoiceDeck/VoiceDeckManager/session.py`, `Nodes/VoiceDeck/voice_deck_frontend/app.py`
- `Nodes/CloudDispatcher/CloudDispatcherManager/dispatcher.py`, `Nodes/CloudDispatcher/cloud_dispatcher_frontend/app.py`
