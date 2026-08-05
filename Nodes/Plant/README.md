# PlantManager

Centralized agent pool. **PlantManager** reads Redis stream `WORKORDER`, prepares git worktrees under `Floor/`, and spins one-shot **LiveHarness** Docker sandboxes. LiveHarness resolves ticket details from the `WORKORDER` entry referenced by `LIVEHARNESS:<GUID>.ticket_id`, then publishes to stream `FINISHED:<repo>` and deletes the hash.

```
WORKORDER (new_wt=true)
    → ensure Floor/<repo>/.bare + wt/dev + wt/agents + wt/tickets/
    → Floor/<repo>/wt/tickets/<ticket_name>  (branch ticket/<ticket_name> from agents)
    → HSET LIVEHARNESS:<GUID> {ticket_id, status, error}
    → Docker sandbox mounts ticket worktree
    → LiveHarness loads WORKORDER via ticket_id, runs agent
    → XADD FINISHED:<repo>, DEL hash, exits

WORKORDER (new_wt=false)
    → mount existing absolute wt (no new worktree)
    → same LIVEHARNESS / FINISHED path
```

TicketDispatcher publishes new-ticket `WORKORDER`s (`new_wt=true`). MergeManager publishes conflict-resolution `WORKORDER`s (`new_wt=false`) and consumes `FINISHED:<repo>`.

## Prerequisites

- Python 3.12+
- Git
- Docker
- A **local Redis** already running on `localhost:6379` (this project does **not** start Redis)
- A Cursor API key

## Setup

```powershell
cd C:\Users\GoodSirington\Desktop\Plant
pip install -r requirements.txt
```

Create a `.env` in the project root (gitignored):

```env
CURSOR_API_KEY=cursor_...
```

Optional:

```env
REDIS_URL=redis://localhost:6379/0
```

## Build the agent image

```powershell
python -m PlantManager build
```

Builds `plant-agent:latest` (Python, git, Cursor CLI, LiveHarness, `redis_packets.py`).

After changing `LiveHarness/`, `PlantManager/`, `redis_packets.py`, the `Dockerfile`, or `requirements.txt`, rebuild so containers pick up the new code.

## Run PlantManager

```powershell
python -m PlantManager
# or:
python -m PlantManager run
```

## Redis contracts

### WORKORDER

Redis stream. Consumer group `plant`.

| Field | Purpose |
|-------|---------|
| `repo` | Floor repo name |
| `URL` | Remote URL (required when creating Floor / new_wt) |
| `new_wt` | `true` create ticket worktree from agents; `false` use existing `wt` |
| `wt` | Absolute host worktree path when `new_wt=false` |
| `ticket_name` | Ticket name |
| `instructions` | Agent prompt |
| `model` | Model id (default `auto`) |

```powershell
redis-cli XADD WORKORDER * repo Helmsman URL https://github.com/SamJBoyer/Helmsman.git new_wt true wt "" ticket_name 1 instructions "Create harness-smoke.txt with the text ok" model auto
```

### LIVEHARNESS:\<GUID\>

| Field | Purpose |
|-------|---------|
| `ticket_id` | Stream id of the target `WORKORDER` entry |
| `status` | `starting` / `running` / `finished` / `error` / … |
| `error` | Error message if any |

LiveHarness loads `ticket_name`, `instructions`, and `model` from `WORKORDER` via `ticket_id`. Host paths for FINISHED are passed into the container as `HOST_WT` / `HOST_AGENT_DIR`.

### FINISHED:\<REPO\>

Redis **stream** (not a list):

| Field | Purpose |
|-------|---------|
| `ticket_name` | Ticket name |
| `ticket_id` | Originating WORKORDER stream id |
| `wt` | Absolute path to the ticket worktree |
| `agent_dir` | Absolute path to the agents worktree |

```powershell
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
| `PlantManager/` | WORKORDER poller, Floor setup, Docker spin-up |
| `LiveHarness/` | One-shot GUID hash → WORKORDER → Cursor agent → FINISHED |
| `redis_packets.py` | Shared Redis field builders/parsers |
| `Dockerfile` | Sandbox image; entrypoint `python -m LiveHarness` |
| `Floor/` | Local bare clones + worktrees (gitignored) |
| `.env` | Secrets (gitignored) |

## Notes

- Containers reach host Redis via `host.docker.internal` (`REDIS_URL_CONTAINER` to override).
- Host-side tools use `REDIS_URL` (default `redis://localhost:6379/0`).
- LiveHarness exits when the job finishes; `--rm` removes the container.
- This project never starts or manages Redis for you.
