# Contracts

Central registry of MegaDesk cross-module contracts:

1. **Python package `megadesk-contracts`** — installable shared library (`FeSpec` / `BeSpec`, `MegaDesk.nodes` discovery, Supervisor Redis client, Dear PyGui frame pump).
2. **Redis docs** (`redis/`) — expected Redis package layouts and IPC conventions.

Implementation helpers live in module-local `redis_packets.py` files; when those diverge, treat this folder as the intended contract and reconcile the code.

## Install

```bash
conda activate <MegaDesk-env>
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
| [`redis/plant-pipeline.md`](redis/plant-pipeline.md) | `WORKORDER` → `LIVEHARNESS:<GUID>` → `FINISHED:<REPO>` |
| [`redis/supervisor.md`](redis/supervisor.md) | Supervisor streams (`LAUNCHREQUEST` / `KILLREQUEST`), `RUNNINGNODES:<unique_id>`, alive key |

## Modules that speak Redis

| Module | Role |
|--------|------|
| **TicketDispatcher** | Publishes `WORKORDER` (`new_wt=true`) |
| **Plant / PlantManager** | Consumes `WORKORDER`; writes `LIVEHARNESS:<GUID>` |
| **Plant / LiveHarness** | Reads harness hash + `WORKORDER`; publishes `FINISHED:<REPO>` |
| **MergeManager** | Consumes `FINISHED:<REPO>`; may republish conflict `WORKORDER`s (`new_wt=false`) |
| **Supervisor** | Consumes `LAUNCHREQUEST` / `KILLREQUEST`; writes `RUNNINGNODES:<unique_id>` |
| **MegaDesk canvas (`MegaDesk-Canvas/`)** | On canvas drop of a MegaDesk FE that also exposes a BE, `XADD`s `LAUNCHREQUEST` |
