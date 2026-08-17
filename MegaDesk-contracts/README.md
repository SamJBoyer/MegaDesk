# Contracts

Central registry of MegaDesk cross-module contracts:

1. **Python package `megadesk-contracts`** — installable shared library (`FeSpec` / `BeSpec`, `MegaDesk.nodes` discovery, Supervisor Redis client, Dear PyGui frame pump).
2. **Wire modules** (`megadesk_contracts/wire/`) — one canonical definition per stream, with build and parse helpers, imported by both halves of a node. New streams belong here, not in a per-node `redis_packets.py`; the duplicate-module problem `tests/test_wire_contract.py` exists to catch is what that rule prevents.
3. **Redis docs** (`redis/`) — expected Redis package layouts and IPC conventions (DB 0 ephemeral / DB 1 persistent for Supervisor).
4. **Test harness** (`megadesk_contracts/testing/`) — drives a real canvas in-process so integration tests can cross GUI and stream seams. See [`Docs/integration_testing.md`](../Docs/integration_testing.md).

Implementation helpers live in module-local `redis_packets.py` files; when those diverge, treat this folder as the intended contract and reconcile the code.

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
| [`megadesk_contracts/wire/`](megadesk_contracts/wire/) | `code_scope`, `voice`, `cloud` — canonical stream field sets with build / parse helpers |
| [`megadesk_contracts/repo.py`](megadesk_contracts/repo.py) | `ensure_clone` / `refresh_clone` for disposable read-only clones |
| [`megadesk_contracts/agent_errors.py`](megadesk_contracts/agent_errors.py) | `AgentStartupError` (never ran, maybe retry) vs `AgentRunError` (ran and failed) |
| [`megadesk_contracts/realtime.py`](megadesk_contracts/realtime.py) | `RealtimeTransport` — the speech-to-speech surface VoiceDeck is written against |
| [`megadesk_contracts/cloud_runtime.py`](megadesk_contracts/cloud_runtime.py) | `CloudRuntime` — the launch / poll / cancel surface CloudDispatcher is written against |
| [`megadesk_contracts/testing/`](megadesk_contracts/testing/) | `CanvasHarness`, `NodeDriver`, `GitFloor`, and the fakes (`FakeGh`, `FakeAgent`, `FakeCodeAgent`, `FakeRealtime`, `FakeCloudRuntime`) — imports nothing from any node |
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
| **MegaDesk canvas (`MegaDesk-Canvas/`)** | On graph drop/open of a MegaDesk FE that also exposes a BE, `XADD`s `LAUNCHREQUEST` with `FeSpec.backend_parameters` |
| **CodeScope** | Consumes `CODEQ:ASK`, publishes `CODEQ:ANSWER` on DB 0; owns `CODESCOPE:SESSION:<id>` on DB 1 |
| **VoiceDeck** | `VOICE:CONTROL` / `VOICE:EVENT` on DB 0; publishes `CODEQ:ASK`, writes `CLOUDDRAFT:<order_id>` on DB 1. Never puts audio on Redis |
| **CloudDispatcher** | Consumes `CLOUDORDER`, publishes `CLOUDFINISHED` on DB 0; owns `CLOUDRUN:<agent_id>` on DB 1 |
