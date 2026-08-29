# Sargent

A chat window that takes a rough human prompt and returns a clearer, better-structured
version of the same request. One OpenAI Chat Completions call — not a Cursor agent.

## Halves

| Half | What it does |
|------|--------------|
| FE (`sargent_frontend/app.py`) | Compact chat: type a prompt, show the rewrite |
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
