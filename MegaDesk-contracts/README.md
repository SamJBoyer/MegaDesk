# Contracts

Central registry of MegaDesk cross-module contracts:

1. **Python package `megadesk-contracts`** — installable shared library (`FeSpec` / `BeSpec`, `MegaDesk.nodes` discovery, Supervisor Redis client, Dear PyGui frame pump).
2. **Wire modules** (`megadesk_contracts/wire/`) — one canonical definition per stream, with build and parse helpers, imported by both halves of a node. New streams belong here. A node must never ship its own copy: `WORKORDER` used to be defined twice, in MachineFactory and MergeManager, and two copies of a contract both sides of a stream depend on is exactly the seam that drifts silently.
3. **Redis docs** (`redis/`) — expected Redis package layouts and IPC conventions (DB 0 ephemeral / DB 1 persistent for Supervisor).
4. **Test harness** (`megadesk_contracts/testing/`) — drives a real canvas in-process so integration tests can cross GUI and stream seams. See [`Docs/integration_testing.md`](../Docs/integration_testing.md).

## Install

```bash
conda activate MEGADESK
pip install -e MegaDesk-contracts
```

Import as:

```python
from megadesk_contracts import (
    FeSpec, BeSpec, Mode, frame_pump, SupervisorClient,
    DEFAULT_REDIS_URL, resolve_redis_url,
)
```

## Layout

| Path | Contents |
|------|----------|
| [`megadesk_contracts/`](megadesk_contracts/) | Installable Python package (`megadesk-contracts`) |
| [`megadesk_contracts/node_runtime.py`](megadesk_contracts/node_runtime.py) | `NodeRuntime` — 5s heartbeat + Redis kill switch for every Python BE |
| [`megadesk_contracts/parameters.py`](megadesk_contracts/parameters.py) | Graph parameter names (`parameters.yaml`) and the Redis/env JSON packet |
| [`megadesk_contracts/paths.py`](megadesk_contracts/paths.py) | `resolve_canvas_root()` / `resolve_logs_root()` — Supervisor cwd and worktree `Logs/` follow the running checkout, not another worktree |
| [`megadesk_contracts/log_session.py`](megadesk_contracts/log_session.py) | Supervisor-generation log sessions (`begin_log_session`, `Logs/CURRENT`, `{node}.md`) |
| [`megadesk_contracts/wire/`](megadesk_contracts/wire/) | `factory`, `machine`, `cloud`, `code_scope`, `voice` — canonical stream field sets with build / parse helpers |
| [`megadesk_contracts/wire/factory.py`](megadesk_contracts/wire/factory.py) | The status vocabulary both factories report in, and `normalize_status` |
| [`megadesk_contracts/repo.py`](megadesk_contracts/repo.py) | `ensure_clone` / `refresh_clone` for disposable read-only clones |
| [`megadesk_contracts/agent_errors.py`](megadesk_contracts/agent_errors.py) | `AgentStartupError` (never ran, maybe retry) vs `AgentRunError` (ran and failed) |
| [`megadesk_contracts/realtime.py`](megadesk_contracts/realtime.py) | `RealtimeTransport` — the speech-to-speech surface VoiceDeck is written against |
| [`megadesk_contracts/factory.py`](megadesk_contracts/factory.py) | `AgentFactory` — the launch / poll / cancel surface both factories implement, plus `RunHandle` / `RunStatus` |
| [`megadesk_contracts/testing/`](megadesk_contracts/testing/) | `CanvasHarness`, `NodeDriver`, `GitFloor`, and the fakes (`FakeGh`, `FakeAgent`, `FakeCodeAgent`, `FakeRealtime`, `FakeCloudFactory`, `FakeMachineFactory`) — imports nothing from any node |
| [`redis/README.md`](redis/README.md) | Connection defaults, DB split, encoding rules, package index |
| [`redis/machine-factory-pipeline.md`](redis/machine-factory-pipeline.md) | `WORKORDER` → `AGENTHANDLER:<GUID>` → `FINISHED:<REPO>` |
| [`redis/supervisor.md`](redis/supervisor.md) | Supervisor streams (DB 0), `RUNNINGNODES` / singleton / alive (DB 1) |

## Modules that speak Redis

| Module | Role |
|--------|------|
| **TicketDispatcher** | Publishes `WORKORDER` (`new_wt=true`) and `CLOUDORDER` on DB 0 |
| **MachineFactory / MachineFactoryManager** | Consumes `WORKORDER`; writes `AGENTHANDLER:<GUID>` on DB 0, and reaps the ones whose sandbox is gone |
| **MachineFactory / AgentHandler** | Reads `AGENTHANDLER:<GUID>` + `WORKORDER`; publishes `FINISHED:<REPO>` on DB 0 |
| **MergeManager** | Consumes `FINISHED:<REPO>`; may republish conflict `WORKORDER`s (`new_wt=false`) on DB 0 |
| **Supervisor** (Canvas-owned, `MegaDesk-Canvas/supervisor/`) | Consumes `LAUNCHREQUEST` / `KILLREQUEST` on DB 0; writes `RUNNINGNODES:<unique_id>` + singleton/alive on DB 1. Bootstrapped by canvas startup via `ensure_supervisor_running()` — not a Catalog node. |
| **MegaDesk canvas (`MegaDesk-Canvas/`)** | On graph drop/open of a MegaDesk FE that also exposes a BE, `XADD`s `LAUNCHREQUEST` with `FeSpec.backend_parameters` |
| **CodeScope** | Consumes `CODEQ:ASK`, publishes `CODEQ:ANSWER` on DB 0; owns `CODESCOPE:SESSION:<id>` on DB 1 |
| **VoiceDeck** | `VOICE:CONTROL` / `VOICE:EVENT` on DB 0; publishes `CODEQ:ASK`, writes `CLOUDDRAFT:<order_id>` on DB 1. Never puts audio on Redis |
| **CloudFactory** | Consumes `CLOUDORDER`, publishes `CLOUDFINISHED` on DB 0; owns `CLOUDRUN:<agent_id>` on DB 1 |
