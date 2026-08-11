# MissionControl

Centralized agent pool. **MissionControlManager** reads Redis stream `WORKORDER`, prepares git worktrees under `Floor/`, and spins one-shot **AgentHandler** Docker sandboxes. AgentHandler resolves ticket details from the `WORKORDER` entry referenced by `AGENTHANDLER:<GUID>.ticket_id`, then publishes to stream `FINISHED:<repo>` and deletes the hash.

```
WORKORDER (new_wt=true)
    → ensure Floor/<repo>/.bare + wt/dev + wt/agents + wt/tickets/
    → Floor/<repo>/wt/tickets/<ticket_name>  (branch ticket/<ticket_name> from agents)
    → HSET AGENTHANDLER:<GUID> {ticket_id, status, error}
    → Docker sandbox mounts ticket worktree
    → AgentHandler loads WORKORDER via ticket_id, runs agent
    → XADD FINISHED:<repo>, DEL hash, exits

WORKORDER (new_wt=false)
    → mount existing absolute wt (no new worktree)
    → same AGENTHANDLER / FINISHED path
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
cd C:\Users\GoodSirington\Desktop\MissionControl
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
python -m MissionControlManager build
```

Builds `mission-control-agent:latest` (Python, git, Cursor CLI, AgentHandler, `redis_packets.py`).

After changing `AgentHandler/`, `MissionControlManager/`, `redis_packets.py`, the `Dockerfile`, or `requirements.txt`, rebuild so containers pick up the new code.

## Run MissionControlManager

```powershell
python -m MissionControlManager
# or:
python -m MissionControlManager run
```

## Redis contracts

### WORKORDER

Redis stream. Consumer group `mission_control`.

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

### AGENTHANDLER:\<GUID\>

| Field | Purpose |
|-------|---------|
| `ticket_id` | Stream id of the target `WORKORDER` entry |
| `status` | `starting` / `running` / `finished` / `error` / … |
| `error` | Error message if any |

AgentHandler loads `ticket_name`, `instructions`, and `model` from `WORKORDER` via `ticket_id`. Host paths for FINISHED are passed into the container as `HOST_WT` / `HOST_AGENT_DIR`.

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
| `MissionControlManager/` | WORKORDER poller, Floor setup, Docker spin-up |
| `AgentHandler/` | One-shot GUID hash → WORKORDER → Cursor agent → FINISHED |
| `redis_packets.py` | Shared Redis field builders/parsers |
| `Dockerfile` | Sandbox image; entrypoint `python -m AgentHandler` |
| `Floor/` | Local bare clones + worktrees (gitignored) |
| `.env` | Secrets (gitignored) |

## Notes

- Containers reach host Redis via `host.docker.internal` (`REDIS_URL_CONTAINER` to override).
- Host-side tools use `REDIS_URL` (default `redis://localhost:6379/0`).
- AgentHandler exits when the job finishes; `--rm` removes the container.
- This project never starts or manages Redis for you.
