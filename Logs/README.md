# Logs

Supervisor-owned session transcripts for this worktree.

- `CURRENT` — JSON pointer at the live session folder (`session`, `started_at`, `supervisor_pid`).
- `{timestamp}Z/` — one folder per Supervisor generation. Files are born here and never moved.
- `{node}.md` — one file per node in that session (append). `supervisor.md` and `canvas.md` are the infrastructure logs.
- `agent-{guid}.md` — pretty MachineFactory sandbox transcript (coalesced thinking/assistant).
- `agent-{guid}.tokens.md` — token-by-token SDK stream for the same run.

A session starts when a Supervisor BE starts, not when MegaDesk-Canvas opens. Reopening the canvas while Supervisor is still alive appends to the same session. Read `CURRENT`, then that folder.

See `Docs/node_protocol.md` (Logging standard).
