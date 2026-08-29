# Sargent

Type a rough prompt. Get a cleaner, better-structured version back. One OpenAI
chat-completions call — not a Cursor agent.

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

`prompt_id` is the join key. One ask produces one answer.

## Requirements

- Python 3.10+, Redis at `REDIS_URL`
- `OPENAI_API_KEY` for the BE (`SARGENT_MODEL` overrides the default `gpt-4o`)
- `pip install -e Nodes/Sargent[canvas]`
