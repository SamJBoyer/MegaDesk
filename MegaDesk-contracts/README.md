# Contracts

Central registry of MegaDesk cross-module contracts:

1. **Python package `megadesk-contracts`** — installable shared library (`FeSpec` / `BeSpec`, `MegaDesk.nodes` discovery, Supervisor Redis client, Dear PyGui frame pump).
2. **Redis docs** (`redis/`) — expected Redis package layouts and IPC conventions (DB 0 ephemeral / DB 1 persistent for Supervisor).

Implementation helpers live in module-local `redis_packets.py` files; when those diverge, treat this folder as the intended contract and reconcile the code.

## Install

```bash
conda activate MEGADESK
pip install -e MegaDesk-contracts
```

Import as:

```python
from megadesk_contracts import FeSpec, BeSpec, Mode, frame_pump, SupervisorClient
```

## Layout

| Path | Contents |
|------|----------|
| [`megadesk_contracts/`](megadesk_contracts/) | Installable Python package (`megadesk-contracts`) |
| [`redis/README.md`](redis/README.md) | Connection defaults, DB split, encoding rules, package index |
| [`redis/mission-control-pipeline.md`](redis/mission-control-pipeline.md) | `WORKORDER` → `AGENTHANDLER:<GUID>` → `FINISHED:<REPO>` |
| [`redis/supervisor.md`](redis/supervisor.md) | Supervisor streams (DB 0), `RUNNINGNODES` / singleton / alive (DB 1) |

## Modules that speak Redis

| Module | Role |
|--------|------|
| **TicketDispatcher** | Publishes `WORKORDER` (`new_wt=true`) on DB 0 |
| **MissionControl / MissionControlManager** | Consumes `WORKORDER`; writes `AGENTHANDLER:<GUID>` on DB 0 |
| **MissionControl / AgentHandler** | Reads `AGENTHANDLER:<GUID>` + `WORKORDER`; publishes `FINISHED:<REPO>` on DB 0 |
| **MergeManager** | Consumes `FINISHED:<REPO>`; may republish conflict `WORKORDER`s (`new_wt=false`) on DB 0 |
| **Supervisor** (Canvas-owned, `MegaDesk-Canvas/supervisor/`) | Consumes `LAUNCHREQUEST` / `KILLREQUEST` on DB 0; writes `RUNNINGNODES:<unique_id>` + singleton/alive on DB 1. Bootstrapped by canvas startup via `ensure_supervisor_running()` — not a Catalog node. |
| **MegaDesk canvas (`MegaDesk-Canvas/`)** | On canvas drop of a MegaDesk FE that also exposes a BE, `XADD`s `LAUNCHREQUEST` |
