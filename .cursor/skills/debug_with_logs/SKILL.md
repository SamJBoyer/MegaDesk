---
name: debug_with_logs
description: Debug MegaDesk using worktree session transcripts under Logs/. Use when diagnosing node/canvas/supervisor failures, designing fixes from runtime evidence, or when the user mentions logs, CURRENT, agent transcripts, or debugging MegaDesk behavior.
---

# Debug with Logs

Always look at the current log for the most up-to-date information and use this information in your design/debugging.

## Locate the live session

1. Read `Logs/CURRENT` (JSON pointer: `session`, `started_at`, `supervisor_pid`).
2. Open `Logs/{session}/` from that pointer — **not** an older timestamp folder.
3. Prefer these files over Redis guesses, chat memory, or stale session folders.

Older timestamp folders are previous Supervisor generations. Canvas reopen does not rotate logs while the same Supervisor stays alive.

## What lives where

| Source | File |
|--------|------|
| Supervisor | `supervisor.md` |
| Canvas / FE host | `canvas.md` |
| Node BE stdout/stderr | `{endpoint}.md` (e.g. `cloud_factory.md`, `machine_factory.md`) |
| MachineFactory agent | `agent-{guid}.md` (pretty), `agent-{guid}.tokens.md` (token stream) |

Authority: `Docs/node_protocol.md` (Logging standard). Do not treat Redis stream bodies as log substitutes.

## Workflow

1. Read `Logs/CURRENT` → resolve the live folder.
2. Skim the relevant `{endpoint}.md` / `canvas.md` / `supervisor.md` / `agent-*.md` for the failing path.
3. Ground the diagnosis and any design or code change in what those files actually show (errors, launch/exit banners, stack traces, agent steps).
4. After a fix and a re-run, re-read `Logs/CURRENT` (session may have rotated) and the new session files before declaring success.

## Do not

- Skip logs and debug from assumptions alone.
- Use a non-current timestamp folder when `Logs/CURRENT` points elsewhere.
- Put log line bodies on Redis streams.
