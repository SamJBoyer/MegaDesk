# CodeScope

Ask questions about a repository in natural language and get answers grounded in
the actual code.

This is a **cloud node**. The process that clones the repo and runs the Cursor
agent is not on this machine — it is an HTTP service (`CODESCOPE_URL`), usually
on AWS Lightsail. The canvas FE and VoiceDeck are clients of that service.

There is no RAG pipeline here, and that is the point. A Cursor agent bound to a
cloned directory already has search and file-read tools over it, so the agent
*is* the retrieval layer. One agent stays warm per session, which is what makes
follow-up questions cheap.

## Halves

| Half | What it does |
|------|--------------|
| FE (`code_scope_frontend/app.py`) | Repo URL intake, asks questions, shows streamed answers. Talks HTTP. |
| HTTP (`CodeScopeManager serve`) | `POST /repos` clones GitHub URLs only, `POST /sessions/{id}/ask` streams sentences. Default bind `127.0.0.1`. OpenAPI docs disabled. |
| Tools (`code_scope_tools/`) | `ask_codebase`, `set_repo`, `dispatch_doc_agent` for VoiceDeck |

There is no Supervisor-launched BE. `get_be_spec()` returns `None`.

The Redis poller (`python -m CodeScopeManager run`) still exists for local
debugging of the old `CODEQ:ASK` path. The canvas does not start it.

## Wire (HTTP)

Canonical answer field names are the same as Redis `CODEQ:ANSWER` (see
`megadesk_contracts.wire.code_scope`). Auth is `Authorization: Bearer
$CODESCOPE_API_TOKEN`. Full routes: [`Docs/codescope_service.md`](../../../Docs/codescope_service.md).

| Env | Who |
|-----|-----|
| `CODESCOPE_URL` | Canvas FE + VoiceDeck. Base URL of the service (`http://host:8080`). |
| `CODESCOPE_API_TOKEN` | Shared bearer token. Same value the service was started with. |
| `CURSOR_API_KEY` | Service host only. |
| `SCOPE_ROOT` | Service host only. Clone + `sessions.json` directory. |

## Clones

Clones live under `SCOPE_ROOT/<repo>/` on the **service host**, shallow by
default. They are disposable: `sync` hard-resets to the remote default branch.

## Requirements

- Python 3.10+, git on PATH (on the service host)
- `CURSOR_API_KEY` on the service host
- `CODESCOPE_URL` + `CODESCOPE_API_TOKEN` on the machine running MegaDesk
- `pip install -e Nodes/Cloud/CodeScope[canvas]`
- HTTP service: `python -m CodeScopeManager serve` (listens on `127.0.0.1:8080`;
  pass `--host 0.0.0.0` only when you intend all-interfaces) — see
  [`Docs/codescope_service.md`](../../../Docs/codescope_service.md)

`POST /repos` accepts `https://github.com/…`, `https://www.github.com/…`, and
`git@github.com:…` only. Local directories remain valid for the integration
suite. `/health` is public; `/docs` / `/redoc` / `/openapi.json` are off.
