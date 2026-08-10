# Redis conventions

MegaDesk processes communicate over a shared local Redis. Two families of packages coexist on the same server, split across Redis databases:

1. **Plant pipeline** (streams + short-lived hashes on **DB 0**) — TicketDispatcher, Plant, MergeManager
2. **Supervisor** (streams on **DB 0**; RUNNINGNODES / singleton / alive on **DB 1**) — Canvas-owned Supervisor BE and launched BE nodes

## Connection

| Setting | Convention |
|---------|------------|
| Host | `localhost` |
| Port | `6379` |
| Default URL (ephemeral) | `redis://localhost:6379/0` |
| Env override (host tools) | `REDIS_URL` |
| Env override (Docker → host) | `REDIS_URL_CONTAINER` / container `REDIS_URL`, typically `redis://host.docker.internal:6379/0` |

Plant, TicketDispatcher, and MergeManager use **DB 0** (`redis://localhost:6379/0`) for workorders and related traffic. They **do not** start Redis. The Canvas-owned Supervisor BE may attach to an existing localhost Redis or provision Docker Redis + Redis Insight if none is reachable.

## Databases

| DB | Use | Constants (`megadesk_contracts.supervisor_client`) |
|----|-----|-----------------------------------------------------|
| **0** (ephemeral) | Default realtime traffic: Plant `WORKORDER` / `LIVEHARNESS` / `FINISHED`; Supervisor streams `LAUNCHREQUEST` / `KILLREQUEST` / `NODEEXIT` | `REDIS_DB_EPHEMERAL` |
| **1** (persistent) | `GBD:SUPERVISOR:SINGLETON`, `GBD:SUPERVISOR:ALIVE`, `RUNNINGNODES:<unique_id>` | `REDIS_DB_PERSISTENT` |

## Encoding

- All field values are **strings** (Redis hash/stream convention).
- Clients use `decode_responses=True`.
- Booleans on the wire are `"true"` / `"false"` (see `bool_field` in `redis_packets.py`).
- Empty string `""` is used for unused optional fields (e.g. `wt` when `new_wt=true`, Supervisor `parameters`).

## Package index

| Package | Redis type | Key / pattern | DB | Doc |
|---------|------------|---------------|----|-----|
| `WORKORDER` | stream | `WORKORDER` | 0 | [plant-pipeline.md](plant-pipeline.md#workorder) |
| `LIVEHARNESS` | hash | `LIVEHARNESS:<GUID>` | 0 | [plant-pipeline.md](plant-pipeline.md#liveharnessguid) |
| `FINISHED` | stream | `FINISHED:<REPO>` | 0 | [plant-pipeline.md](plant-pipeline.md#finishedrepo) |
| `LAUNCHREQUEST` | stream | `LAUNCHREQUEST` | 0 | [supervisor.md](supervisor.md#launchrequest) |
| `KILLREQUEST` | stream | `KILLREQUEST` | 0 | [supervisor.md](supervisor.md#killrequest) |
| `NODEEXIT` | stream | `NODEEXIT` | 0 | [supervisor.md](supervisor.md#nodeexit) |
| `RUNNINGNODES` | hash | `RUNNINGNODES:<unique_id>` | 1 | [supervisor.md](supervisor.md#runningnodesunique_id) |
| Supervisor singleton | string | `GBD:SUPERVISOR:SINGLETON` | 1 | [supervisor.md](supervisor.md#gbdsupervisorsingleton) |
| Supervisor alive | string (TTL) | `GBD:SUPERVISOR:ALIVE` | 1 | [supervisor.md](supervisor.md#gbdsupervisoralive) |

## Code references

Canonical field builders/parsers (duplicated intentionally today):

- `Nodes/Plant/redis_packets.py`
- `Nodes/MergeManager/redis_packets.py`

Supervisor keys/streams (Canvas-owned BE):

- `MegaDesk-Canvas/supervisor/redis_provision.py`
- `MegaDesk-Canvas/supervisor/stream_server.py`
- `MegaDesk-Canvas/supervisor/engine.py`
- `MegaDesk-contracts/megadesk_contracts/supervisor_client.py`

## Obsolete names (do not use)

Older prompts mentioned `WORKREQUEST` and `MERGEREQUEST:*`. The live contract is **`WORKORDER`** and **`FINISHED:<REPO>`** only.

Older Supervisor docs mentioned YAML manifests (`register_manifest` / `execute_manifest` / `PARAMETERS_*`), Pub/Sub `launch_node` / `stop_node` / `acknowledgements` / `KILLALL` / `GBD:COMMANDER:ALIVE`, and a Catalog node under `Nodes/Supervisor/`. The live contract is Canvas-owned Supervisor (`python -m supervisor`) with **`LAUNCHREQUEST`** / **`KILLREQUEST`** / **`NODEEXIT`** on DB 0 and **`RUNNINGNODES:<unique_id>`** / **`GBD:SUPERVISOR:SINGLETON`** / **`GBD:SUPERVISOR:ALIVE`** on DB 1.
