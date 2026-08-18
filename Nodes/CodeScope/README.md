# CodeScope

Ask questions about a repository in natural language and get answers grounded in
the actual code.

There is no RAG pipeline here, and that is the point. A local Cursor agent bound
to a cloned directory already has search and file-read tools over it, so the
agent *is* the retrieval layer: no embeddings, no vector store, nothing to keep
in sync. One agent stays warm per session, which is what makes follow-up
questions cheap and gives them conversation memory.

VoiceDeck talks to this node over the same two streams the FE uses, so a spoken
question and a typed one take exactly the same path.

## Halves

| Half | What it does |
|------|--------------|
| FE (`code_scope_frontend/app.py`) | Repo URL intake, clones into `Scope/<repo>/`, publishes the session, asks questions, shows streamed answers |
| BE (`CodeScopeManager/`) | Consumes `CODEQ:ASK`, keeps one Cursor agent per session, streams `CODEQ:ANSWER` |

The FE clones rather than the BE because the FE is the half that has the URL and
already runs a background thread. The BE never needs the URL — it reads
`clone_path` off the session hash.

## Wire

Canonical definitions live in `megadesk_contracts.wire.code_scope`, where every
stream in this repo is defined exactly once.

| Package | Type | Key | DB |
|---------|------|-----|----|
| `CODEQ:ASK` | stream | `CODEQ:ASK` (group `code_scope`) | ephemeral (`REDIS_URL`) |
| `CODEQ:ANSWER` | stream | `CODEQ:ANSWER` (no group) | same |
| session | hash | `CODESCOPE:SESSION:<session_id>` | persistent (1 on the live pair) |

`CODEQ:ANSWER` has no consumer group on purpose: the FE and VoiceDeck both read
every answer, and a group would let one steal from the other.

One question produces several `CODEQ:ANSWER` entries as the agent's text arrives,
cut at sentence boundaries because that is the unit VoiceDeck speaks. `final`
marks the last entry for a `question_id`; an empty answer is legal only as that
terminator.

## Clones

Clones live under `Scope/<repo>/` (`SCOPE_ROOT` overrides), shallow by default.
They are **disposable**: `sync` hard-resets to the remote default branch and
cleans untracked files. Nothing you care about should live there, which is also
the safety net for the agent's write tools — the prompt tells it to read only,
and the clone makes it not matter if it forgets.

This is deliberately not MachineFactory's `Floor/` worktree farm: answering
questions needs no branches, and sharing a worktree with a writing agent would
race it.

## Requirements

- Python 3.10+, git on PATH
- Redis at `REDIS_URL`
- `CURSOR_API_KEY` for the BE
- `pip install -e Nodes/CodeScope[canvas]`

## Notes

- Questions are answered one at a time. A question asked mid-answer waits, which
  is what a conversation expects and keeps one agent from being asked two things
  at once.
- Every ask is acked, and every ask that cannot be answered gets an error answer
  first — a person waiting to hear a reply should never be left with only a log
  line on the BE.
- `agent_id` is persisted on the session hash so a restarted BE calls
  `Agent.resume` instead of starting cold.
