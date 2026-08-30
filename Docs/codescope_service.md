# CodeScope HTTP service

A public-ish HTTP wrapper around the same local Cursor agent the canvas CodeScope
BE already runs. No Redis, no VoiceDeck, no CloudFactory. Question in, streamed
sentences out.

The agent is **local** (`AsyncClient.launch_bridge` + a git clone on disk). The
process that serves HTTP must have git, disk, and `CURSOR_API_KEY`. That is why
v1 on AWS is a **Lightsail Ubuntu VM**, not Lambda or Fargate.

Canvas CodeScope FE and VoiceDeck talk HTTP (`CODESCOPE_URL`). They do not
publish `CODEQ:ASK`. The Redis poller (`python -m CodeScopeManager run`) is a
local debug path only.

## What you get

| Method | Path | Auth | Returns |
|---|---|---|---|
| `GET` | `/health` | no | `{"ok": true}` |
| `GET` | `/repos` | Bearer | `{ "repos": [ {session_id, repo, status, model} ] }` |
| `POST` | `/repos` | Bearer | `{session_id, repo, status, model}` after clone |
| `GET` | `/sessions/{id}` | Bearer | one session |
| `POST` | `/sessions/{id}/ask` | Bearer | SSE: CODEQ:ANSWER fields (`answer`, `final`, `status`, …) |

`POST /repos` body: `{"url": "https://github.com/org/repo"}` — GitHub https/SSH
only. Optional `"model"`. Non-GitHub `https://` and `file://` return 400.
`POST .../ask` body: `{"question": "..."}`. Optional `"mode"` (`answer` or
`propose_ticket`). OpenAPI `/docs` is disabled.

Header on every route except `/health`:

```text
Authorization: Bearer <CODESCOPE_API_TOKEN>
```

SSE events are one JSON object per `data:` line, same field names as Redis
`CODEQ:ANSWER`. `final` is `"true"` on the last event only.

## Secrets

| Env | Who |
|---|---|
| `CODESCOPE_API_TOKEN` | Required to start `serve`. A long random string you mint. Stops anonymous internet scanners from driving the agent. |
| `CURSOR_API_KEY` | Required for a real answer. Same User-level key MegaDesk already uses. |
| `SCOPE_ROOT` | Optional. Clone + `sessions.json` directory. Defaults to `Nodes/Cloud/CodeScope/Scope` locally, `/data/scope` in Docker. |
| `GH_TOKEN` / `GITHUB_TOKEN` | Only if you clone private GitHub repos. Skip for a public-repo smoke test. |

Never commit these. Never put AWS access keys in the app; the VM does not need
them.

Mint a token (PowerShell):

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

## Local (this PC, MEGADESK env)

Supervisor should be down so a live CodeScope BE is not also holding clones:

```bash
conda activate MEGADESK
python scripts/down_nodes.py
python scripts/refresh_nodes.py
```

```powershell
$env:CODESCOPE_API_TOKEN = "<paste the token>"
# CURSOR_API_KEY should already be in your User environment.
python -m CodeScopeManager serve --port 8080
# Default bind is 127.0.0.1. Pass --host 0.0.0.0 only when you need all-interfaces.
```

In another shell:

```powershell
curl -s http://127.0.0.1:8080/health
curl -s -H "Authorization: Bearer $env:CODESCOPE_API_TOKEN" -H "Content-Type: application/json" -d "{\"url\":\"https://github.com/octocat/Hello-World\"}" http://127.0.0.1:8080/repos
```

Copy `session_id` from that JSON, then (PowerShell has no `curl -N`; use this):

```powershell
curl.exe -N -H "Authorization: Bearer $env:CODESCOPE_API_TOKEN" -H "Content-Type: application/json" -d "{\"question\":\"What does this repo do?\"}" http://127.0.0.1:8080/sessions/<session_id>/ask
```

A real Cursor answer takes seconds to a minute. The HTTP tests in
`tests/test_codescope_http.py` never call Cursor; they clone a local git fixture
and stream canned sentences.

```bash
conda activate MEGADESK
pytest tests/test_codescope_http.py
```

## Docker image (same bits you will run on Lightsail)

From the **worktree root** (needs `MegaDesk-Contracts` and `Nodes/Cloud/CodeScope`):

```bash
docker build -f Nodes/Cloud/CodeScope/Dockerfile -t codescope-http .
docker run --rm -p 127.0.0.1:8080:8080 -v codescope-data:/data/scope `
  -e CODESCOPE_API_TOKEN -e CURSOR_API_KEY codescope-http `
  python -m CodeScopeManager serve --host 0.0.0.0 --port 8080
```

Then the same curls against `http://127.0.0.1:8080`.

## AWS Lightsail (you do this)

Do these in order. Stop and say so if a screen does not match.

1. Finish AWS signup (email, phone, credit card). Lightsail bills while the
   instance exists, even if you are not hitting it.
2. Root account: enable MFA. Billing → Budgets → a $20 alarm.
3. IAM → create user `megadesk-admin` with AdministratorAccess; enable MFA.
   Sign in as that user. Do not use the root user day to day.
4. Search **Lightsail**. Region: `us-east-1` unless you have a reason otherwise.
5. Create instance: **OS only → Ubuntu 24.04**. Plan: **$12/mo (4 GB RAM)**.
   Name it `codescope`. A 512 MB/1 GB box will OOM when Cursor starts.
6. Create a **static IP** and attach it to `codescope`. That is the public
   address.
7. Networking → IPv4 firewall: keep SSH (22). Add **Custom TCP 8080** from
   Anywhere for the first smoke test. Later close 8080 and only expose 443.
8. Browser SSH or the downloaded key: log in as `ubuntu@<static-ip>`.
9. On the VM:

```bash
sudo apt-get update
sudo apt-get install -y git ca-certificates curl
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu
# log out and back in so docker works without sudo
```

10. Copy this worktree onto the VM (git clone of your fork, or `scp`), then:

```bash
cd MegaDesk   # or whatever you cloned it as
docker build -f Nodes/Cloud/CodeScope/Dockerfile -t codescope-http .
printf 'CODESCOPE_API_TOKEN=%s\nCURSOR_API_KEY=%s\n' \
  '<token>' '<cursor_...>' > ~/.codescope.env
chmod 600 ~/.codescope.env
docker run -d --name codescope --restart unless-stopped \
  -p 8080:8080 -v codescope-data:/data/scope \
  --env-file ~/.codescope.env codescope-http \
  python -m CodeScopeManager serve --host 0.0.0.0 --port 8080
```

11. From your PC:

```powershell
curl -s http://<static-ip>:8080/health
```

That is “connected to AWS”: this PC is a client; clones and the Cursor agent
live on the VM.

HTTPS (Caddy + a domain, then close port 8080) is a follow-up after health
returns `{"ok": true}` over HTTP.

## After this works

Set `CODESCOPE_URL` and `CODESCOPE_API_TOKEN` on the machine that runs MegaDesk.
VoiceDeck's `ask_codebase` POSTs `/sessions/{id}/ask`; the answer pump reads SSE.
