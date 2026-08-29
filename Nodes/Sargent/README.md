# Sargent

A two-column prompt rewriter: the left box is a rough human prompt, the right
box is a clearer, better-structured version of the same request. Enter or send
publishes the ask; copy puts both panels on the clipboard. One OpenAI Chat
Completions call — not a Cursor agent.

## Halves

| Half | What it does |
|------|--------------|
| FE (`sargent_frontend/app.py`) | Left prompt / right rewrite, send + copy |
| BE (`SargentManager/`) | Consumes `SARGENT:ASK`, calls OpenAI, publishes `SARGENT:ANSWER` |

## Wire

Canonical definitions live in `megadesk_contracts.wire.sargent`.

| Package | Type | Key | DB |
|---------|------|-----|----|
| `SARGENT:ASK` | stream | `SARGENT:ASK` (group `sargent`) | ephemeral (`REDIS_URL`) |
| `SARGENT:ANSWER` | stream | `SARGENT:ANSWER` (no group) | same |

`SARGENT:ANSWER` has no consumer group so any reader can see every rewrite.

## Requirements

- Python 3.10+, Redis at `REDIS_URL`
- `OPENAI_API_KEY` for the BE (`SARGENT_MODEL` overrides the default `gpt-4o`)
- `pip install -e Nodes/Sargent[canvas]`
