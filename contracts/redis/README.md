# Redis conventions

MegaDesk processes communicate over a shared local Redis. Two families of packages coexist on the same server:

1. **Plant pipeline** (streams + short-lived hashes) — TicketDispatcher, Plant, MergeManager
2. **Supervisor** (streams + RUNNINGNODES hashes) — Supervisor BE and launched BE nodes

## Connection

| Setting | Convention |
|---------|------------|
| Host | `localhost` |
| Port | `6379` |
| Default URL | `redis://localhost:6379/0` |
| Env override (host tools) | `REDIS_URL` |
| Env override (Docker → host) | `REDIS_URL_CONTAINER` / container `REDIS_URL`, typically `redis://host.docker.internal:6379/0` |

Plant, TicketDispatcher, and MergeManager **do not** start Redis. Supervisor may attach to an existing localhost Redis or provision Docker Redis + Redis Insight if none is reachable.

## Databases

| DB | Use |
|----|------|
| **0** | Realtime traffic: Plant streams/hashes, Supervisor streams/hashes, supervisor alive key |

## Encoding

- All field values are **strings** (Redis hash/stream convention).
- Clients use `decode_responses=True`.
- Booleans on the wire are `"true"` / `"false"` (see `bool_field` in `redis_packets.py`).
- Empty string `""` is used for unused optional fields (e.g. `wt` when `new_wt=true`, Supervisor `parameters`).

## Package index

| Package | Redis type | Key / pattern | Doc |
|---------|------------|---------------|-----|
| `WORKORDER` | stream | `WORKORDER` | [plant-pipeline.md](plant-pipeline.md#workorder) |
| `LIVEHARNESS` | hash | `LIVEHARNESS:<GUID>` | [plant-pipeline.md](plant-pipeline.md#liveharnessguid) |
| `FINISHED` | stream | `FINISHED:<REPO>` | [plant-pipeline.md](plant-pipeline.md#finishedrepo) |
| `LAUNCHREQUEST` | stream | `LAUNCHREQUEST` | [supervisor.md](supervisor.md#launchrequest) |
| `KILLREQUEST` | stream | `KILLREQUEST` | [supervisor.md](supervisor.md#killrequest) |
| `RUNNINGNODES` | hash | `RUNNINGNODES:<unique_id>` | [supervisor.md](supervisor.md#runningnodesunique_id) |
| Supervisor alive | string (TTL) | `GBD:SUPERVISOR:ALIVE` on DB 0 | [supervisor.md](supervisor.md#gbdsupervisoralive) |

## Code references

Canonical field builders/parsers (duplicated intentionally today):

- `Nodes/Plant/redis_packets.py`
- `Nodes/MergeManager/redis_packets.py`

Supervisor keys/streams:

- `Nodes/Supervisor/backend/redis_provision.py`
- `Nodes/Supervisor/backend/stream_server.py`
- `Nodes/Supervisor/backend/engine.py`
- `src/megadesk/supervisor_client.py`

## Obsolete names (do not use)

Older prompts mentioned `WORKREQUEST` and `MERGEREQUEST:*`. The live contract is **`WORKORDER`** and **`FINISHED:<REPO>`** only.

Older Supervisor docs mentioned YAML manifests (`register_manifest` / `execute_manifest` / `PARAMETERS_*`) and Pub/Sub `launch_node` / `stop_node` / `acknowledgements` / `KILLALL` / `GBD:COMMANDER:ALIVE`. The live contract is **`LAUNCHREQUEST`** / **`KILLREQUEST`** / **`RUNNINGNODES:<unique_id>`** / **`GBD:SUPERVISOR:ALIVE`**.
