# Sargent

One OpenAI chat-completions call. A rough prompt goes in on `SARGENT:ASK`; the
rewritten prompt comes back on `SARGENT:ANSWER`. Both streams live on the
process ephemeral DB (`REDIS_URL`).

Canonical builders and parsers: `megadesk_contracts.wire.sargent`.

## `SARGENT:ASK`

Consumer group: `sargent`.

| Field | Role |
|-------|------|
| `prompt_id` | Join key minted by the asker |
| `prompt` | The rough text to rewrite |

## `SARGENT:ANSWER`

No consumer group — the FE XREADs.

| Field | Role |
|-------|------|
| `prompt_id` | Matches the ask |
| `rewrite` | Improved prompt, or an error message |
| `status` | `ok` or `error` |
