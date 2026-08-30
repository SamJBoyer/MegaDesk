# MachineFactory

Runs agents on this machine. **MachineFactoryManager** reads Redis stream
`WORKORDER`, starts a one-shot **AgentHandler** Docker sandbox that clones the
named repo, and gives the agent a Redis **sidecar** as `REDIS_URL`. Factory IPC
(WORKORDER / AGENTHANDLER / FINISHED) stays on `MEGADESK_FACTORY_REDIS_URL` (the
host pair). AgentHandler runs a LangGraph work graph selected by `WORKORDER.graph`
(`work` by default, or `massive` for a ticket too big for one workhorse).
`work` is startup → pathfinder → workhorse → git → teardown. `massive` is
startup → orchestrator → dispatcher → ralph (one commit per card) → test →
teardown. Then it publishes `FINISHED:<repo>` with `status` + `pr_url` and
deletes its hashes.

The cloud counterpart is [CloudFactory](../CloudFactory/README.md); what the two
share, and where they honestly differ, is in [Factory](../README.md).

```text
WORKORDER
    → HSET AGENTHANDLER:<guid> {ticket_id, status, error}
    → Docker sandbox + Redis sidecar (clone URL into workspace)
    → AgentHandler loads WORKORDER via ticket_id
    → work graph (`work` or `massive`, from WORKORDER.graph)
    → XADD FINISHED:<repo> {ticket_name, ticket_id, status, pr_url}
    → DEL AGENTHANDLER + GRAPHRUN, exits
```

WorkDispatcher publishes orders. PRManager lists open PRs whose merge-check
`mergeable` check succeeded on the same repo URL and shows/opens the tracked PR.

## Halves

| Half | What it does |
|------|--------------|
| FE (`machine_factory_frontend/app.py`) | Queued WORKORDERs, live agents, sandboxes, error lamp |
| BE (`MachineFactoryManager/`) | Consume `WORKORDER`, start and follow sandboxes with Redis sidecars |

## The handshake, and why the order matters

The sandbox finds its own work by reading `AGENTHANDLER:<guid>` — the guid arrives
in its environment — so the hash is written **before** the container starts. Only
`ticket_id` goes in it: the instructions stay on the `WORKORDER` stream and are
fetched from there, so they can never drift from what was actually ordered.

A missing hash therefore means "no run", which is what makes the FE's live list
truthful without anyone reconciling it.

## Two loops

`poll_orders` turns orders into sandboxes. `poll_runs` reaps runs whose sandbox is
gone — the abnormal path only. A container is not a managed service, so a healthy
sandbox reports its own outcome from inside, where the exit code is, and deletes
its hash on the way out. `poll_runs` covers the case where it never got the
chance, which would otherwise leave a hash claiming a run that stopped existing.
A sandbox is only treated as lost once it has been missing for longer than
`orphan_grace`, because there is a real moment between publishing `FINISHED` and
deleting the hash. `poll_sidecars` reaps Redis sidecars when the agent sandbox is
gone.

## Prerequisites

- Python 3.12+, Git, Docker
- Redis reachable at `REDIS_URL` (default `redis://localhost:6379/0`; this project
  never starts Redis for you)
- `CURSOR_API_KEY` in the process environment, set User-level so MegaDesk and
  Supervisor inherit it:

```powershell
[System.Environment]::SetEnvironmentVariable("CURSOR_API_KEY", "cursor_...", "User")
```

- GitHub credentials for push + PR (`auto_pr` defaults on). The sandbox is
  Linux Docker and cannot use Windows Git Credential Manager, so a public
  clone can succeed and then die at teardown with `could not read Username`.
  Set `GH_TOKEN` / `GITHUB_TOKEN` User-level, or `gh auth login` on the host
  (MachineFactory reads `gh auth token` on the **host** and mounts it as a
  0600 file + `GIT_ASKPASS`; it is never passed as `-e GH_TOKEN` / `-e
  GITHUB_TOKEN`, and never written into git remotes):

```powershell
[System.Environment]::SetEnvironmentVariable("GH_TOKEN", "ghp_...", "User")
```

Restart MegaDesk (and the terminal that launched it) after setting a User env var.

## Build the sandbox image

```powershell
python -m MachineFactoryManager build
# or from the worktree root:
python scripts/rebuild_sandbox.py
```

Builds `machine-factory-agent:latest` — Python, git, the Cursor CLI, `AgentHandler`
and `megadesk-contracts`. The build context is the **worktree root**, not this
folder, because the sandbox installs the same contracts package the manager writes
with; a copied wire module in the image would be a second definition free to
drift. `.dockerignore` at the root keeps large trees out of the context.

Rebuild after changing `AgentHandler/`, `MegaDesk-Contracts/`, the `Dockerfile` or
`requirements-sandbox.txt`.

## Run it

```powershell
python -m MachineFactoryManager        # same as: run
```

## Wire

Defined once in `megadesk_contracts.wire.machine` and imported by every writer —
this node, WorkDispatcher and the factory FEs. Consumer group `machine_factory`.
Per-node progress is a second family, `megadesk_contracts.wire.graph`
(`GRAPHRUN` / `GRAPHEVENT`); see
[`work-graph.md`](../../../MegaDesk-Contracts/redis/work-graph.md).

| Where | Key | Carries |
|-------|-----|---------|
| db 0 stream | `WORKORDER` | `repo`, `URL`, `ticket_name`, `instructions`, `model`, `auto_pr`, `pictures`, `issue`, `graph` |
| db 0 hash | `AGENTHANDLER:<guid>` | `ticket_id`, `status`, `error` |
| db 0 hash | `GRAPHRUN:<guid>` | live work-graph progress (`spec`, `nodes`, `current`, …) |
| db 0 stream | `GRAPHEVENT` | per-node timeline (`guid`, `node`, `status`, `detail`, `ts`) |
| db 0 stream | `FINISHED:<repo>` | `ticket_name`, `ticket_id`, `status`, `pr_url` |

`URL` is always required: the sandbox clones it rather than mounting a host tree.
`auto_pr` defaults true. `status` is the shared factory vocabulary (`queued`,
`running`, `finished`, `error`, `cancelled`, `startup_error`), validated on write.
`pr_url` may be empty on error paths.

`FINISHED` is per-repo rather than one stream so a factory FE can watch one
repo's outcomes. Inside the sandbox, `REDIS_URL` points at the Redis
sidecar; `MEGADESK_FACTORY_REDIS_URL` is the factory bus on the host pair.

```powershell
redis-cli XADD WORKORDER * repo Helmsman URL https://github.com/SamJBoyer/Helmsman.git ticket_name 1 instructions "Create harness-smoke.txt with the text ok" model auto auto_pr true graph work
redis-cli XRANGE FINISHED:Helmsman - +
```

## Layout

| Path | Role |
|------|------|
| `MachineFactoryManager/` | WORKORDER loop, sandbox + sidecar launch, run reaping |
| `MachineFactoryManager/runtime.py` | `AgentFactory` over Docker: launch, poll, cancel |
| `AgentHandler/` | Inside the sandbox: hash → order → clone → work graph → PR → FINISHED. Streams SDK progress into `Logs/{session}/agent-{guid}.md` (pretty) and `agent-{guid}.tokens.md` (token stream). |
| `AgentHandler/repo_clone.py` | Clone, branch, push, open PR inside the sandbox workspace |
| `machine_factory_frontend/` | Canvas monitor (read-only; never consumes WORKORDER). Logs are in the Supervisor Logs tab. |
| `Dockerfile` | Sandbox image; entrypoint `python -m AgentHandler` |

## Testing

`FakeMachineFactory` stands in for the Docker daemon, so
`tests/test_machinefactory_flow.py` exercises the real consumer group, the real
registry and the real FINISHED payloads without a container. Its mirror is
`tests/test_cloudfactory_flow.py`, and the two are meant to be read side by side.

## Notes

- Sandbox `REDIS_URL` is the per-run Redis sidecar; `MEGADESK_FACTORY_REDIS_URL` is
  the factory bus on the host pair, authenticated as Redis ACL user
  `megadesk-factory` (factory keys only — not `SUPERVISOR:*` / `FLUSHDB`).
  Launch fails if the host Redis refuses `ACL SETUSER`.
- `REPO_URL` must be `https://github.com/…`, `https://www.github.com/…`, or
  `git@github.com:…`. `STARTING_REF` is a branch-like token (`^[\w./-]+$`, no
  leading `-`) passed to `git clone --branch`.
- GitHub token is a host file (mode 0600) + `GIT_ASKPASS`. `CURSOR_API_KEY`
  is still injected as container env (Cursor SDK); that remains a residual
  risk (`docker inspect` / in-sandbox `/proc`).
- The image runs as non-root `USER megadesk`. No Docker socket, no `privileged`.
- AgentHandler exits when the job finishes; `--rm` removes the container.
- Containers are labelled `megadesk.run_key=<guid>`, which is how `poll` and
  `cancel` find one again after a manager restart.
- Redis sidecars (`megadesk.redis_for=<guid>`) are reaped when the agent sandbox
  is gone.
