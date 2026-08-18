# MachineFactory

Runs agents on this machine. **MachineFactoryManager** reads Redis stream
`WORKORDER`, prepares a git worktree under `Floor/`, and starts a one-shot
**AgentHandler** Docker sandbox against it. AgentHandler loads the order it was
sent for, runs the agent, publishes `FINISHED:<repo>` and deletes its own hash.

The cloud counterpart is [CloudFactory](../CloudFactory/README.md); what the two
share, and where they honestly differ, is in [Factory](../README.md).

```text
WORKORDER (new_wt=true)
    → ensure Floor/<repo>/.bare + wt/dev + wt/agents + wt/tickets/
    → Floor/<repo>/wt/tickets/<ticket_name>  (branch ticket/<ticket_name> from agents)
    → HSET AGENTHANDLER:<guid> {ticket_id, status, error}
    → Docker sandbox mounts the ticket worktree
    → AgentHandler loads WORKORDER via ticket_id, runs the agent
    → XADD FINISHED:<repo>, DEL hash, exits

WORKORDER (new_wt=false)
    → mount the existing absolute wt (no new worktree)
    → same AGENTHANDLER / FINISHED path
```

TicketDispatcher publishes new-ticket orders (`new_wt=true`). MergeManager
publishes conflict-resolution orders (`new_wt=false`) and consumes
`FINISHED:<repo>`.

## Halves

| Half | What it does |
|------|--------------|
| FE (`machine_factory_frontend/app.py`) | Processed WORKORDERs, live agents, Floor repos, sandboxes |
| BE (`MachineFactoryManager/`) | Consume `WORKORDER`, prepare Floor, start and follow sandboxes |

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
chance, which would otherwise leave a hash claiming a run that stopped existing
and a ticket worktree nobody merges. A sandbox is only treated as lost once it has
been missing for longer than `orphan_grace`, because there is a real moment
between publishing `FINISHED` and deleting the hash.

## Prerequisites

- Python 3.12+, Git, Docker
- Redis reachable at `REDIS_URL` (default `redis://localhost:6379/0`; this project
  never starts Redis for you)
- `CURSOR_API_KEY` in the process environment, set User-level so MegaDesk and
  Supervisor inherit it:

```powershell
[System.Environment]::SetEnvironmentVariable("CURSOR_API_KEY", "cursor_...", "User")
```

Restart MegaDesk (and the terminal that launched it) after setting a User env var.

## Build the sandbox image

```powershell
python -m MachineFactoryManager build
```

Builds `machine-factory-agent:latest` — Python, git, the Cursor CLI, `AgentHandler`
and `megadesk-contracts`. The build context is the **worktree root**, not this
folder, because the sandbox installs the same contracts package the manager writes
with; a copied wire module in the image would be a second definition free to
drift. `.dockerignore` at the root keeps `Floor/` and friends out of the context.

Rebuild after changing `AgentHandler/`, `MegaDesk-contracts/`, the `Dockerfile` or
`requirements-sandbox.txt`.

## Run it

```powershell
python -m MachineFactoryManager        # same as: run
```

## Wire

Defined once in `megadesk_contracts.wire.machine` and imported by every writer —
this node, TicketDispatcher and MergeManager. Consumer group `machine_factory`.

| Where | Key | Carries |
|-------|-----|---------|
| db 0 stream | `WORKORDER` | `repo`, `URL`, `new_wt`, `wt`, `ticket_name`, `instructions`, `model` |
| db 0 hash | `AGENTHANDLER:<guid>` | `ticket_id`, `status`, `error` |
| db 0 stream | `FINISHED:<repo>` | `ticket_name`, `ticket_id`, `wt`, `agent_dir` |

`new_wt=true` creates a ticket worktree from `agents` and requires `URL`;
`new_wt=false` uses the existing absolute `wt`. `status` is the shared factory
vocabulary (`queued`, `running`, `finished`, `error`, `cancelled`,
`startup_error`), validated on write.

`FINISHED` is per-repo rather than one stream because MergeManager watches the
repos it has checked out, not every repo the Floor knows about. Host paths reach
the container as `HOST_WT` / `HOST_AGENT_DIR`, since paths inside it are not paths
anyone else can use.

```powershell
redis-cli XADD WORKORDER * repo Helmsman URL https://github.com/SamJBoyer/Helmsman.git new_wt true wt "" ticket_name 1 instructions "Create harness-smoke.txt with the text ok" model auto
redis-cli XRANGE FINISHED:Helmsman - +
```

## Floor layout

```text
Floor/
  TESTER/
    .bare/
    wt/
      dev/              # branch `dev`
      agents/           # branch `agents`
      tickets/
        1/              # branch `ticket/1` from agents
```

## Layout

| Path | Role |
|------|------|
| `MachineFactoryManager/` | WORKORDER loop, Floor setup, run reaping |
| `MachineFactoryManager/runtime.py` | `AgentFactory` over Docker: launch, poll, cancel |
| `AgentHandler/` | Inside the sandbox: hash → order → Cursor agent → FINISHED. Streams SDK progress into `Logs/{session}/agent-{guid}.md`. |
| `machine_factory_frontend/` | Canvas monitor (read-only; never consumes WORKORDER). Logs are in the Supervisor Logs tab. |
| `Dockerfile` | Sandbox image; entrypoint `python -m AgentHandler` |
| `Floor/` | Local bare clones and worktrees (gitignored) |

## Testing

`FakeMachineFactory` stands in for the Docker daemon, so
`tests/test_machinefactory_flow.py` exercises the real consumer group, a real git
Floor, the real registry and the real FINISHED payloads without a container. Its
mirror is `tests/test_cloudfactory_flow.py`, and the two are meant to be read side
by side.

## Notes

- Containers reach host Redis via `host.docker.internal` (`REDIS_URL_CONTAINER`
  overrides). Sandbox `REDIS_URL` is a leased even DB (the agent's MegaDesk);
  `MEGADESK_FACTORY_REDIS_URL` is the factory bus.
- AgentHandler exits when the job finishes; `--rm` removes the container.
- Containers are labelled `megadesk.run_key=<guid>`, which is how `poll` and
  `cancel` find one again after a manager restart.
