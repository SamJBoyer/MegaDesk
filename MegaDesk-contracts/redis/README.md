# Redis conventions

MegaDesk processes communicate over a shared local Redis. Two families of packages coexist on the same server, split across Redis databases:

1. **MachineFactory pipeline** (streams + short-lived hashes on **DB 0**) — TicketDispatcher, MachineFactory, MergeManager
2. **Supervisor** (streams on **DB 0**; RUNNINGNODES / singleton / alive on **DB 1**) — Canvas-owned Supervisor BE and launched BE nodes
3. **Voice chain** (streams on **DB 0**; session / run / draft hashes on **DB 1**) — CodeScope, VoiceDeck, CloudFactory

Families 1 and 3 carry the two factories, and they are the same shape on purpose:
an order stream, one hash per live run, a finished stream. See
[`Nodes/Factory/README.md`](../../Nodes/Factory/README.md) for why, and for the
status vocabulary they both report in.

## Connection

| Setting | Convention |
|---------|------------|
| Env var | **`REDIS_URL`** (required standard for all clients) |
| Default | `redis://localhost:6379/0` (`DEFAULT_REDIS_URL` in `megadesk_contracts`) |
| Resolve helper | `resolve_redis_url()` — explicit arg → `REDIS_URL` → default |
| DB selection | Same URL; pass `db=` to `Redis.from_url` (`REDIS_DB_EPHEMERAL=0`, `REDIS_DB_PERSISTENT=1`) |
| Docker → host | `REDIS_URL_CONTAINER` / container `REDIS_URL`, typically `redis://host.docker.internal:6379/0` |

Do **not** hardcode host/port. Prefer `redis.Redis.from_url(resolve_redis_url(), …)`.

MachineFactory, TicketDispatcher, MergeManager, `SupervisorClient`, and Supervisor provision all read **`REDIS_URL`**. Nodes use **DB 0** for workorders and related traffic. They **do not** start Redis. The Canvas-owned Supervisor BE may attach to an existing Redis at `REDIS_URL` or (when the URL host is loopback) provision Docker Redis + Redis Insight if none is reachable.

## Databases

| DB | Use | Constants (`megadesk_contracts.supervisor_client`) |
|----|-----|-----------------------------------------------------|
| **0** (ephemeral) | Default realtime traffic: MachineFactory `WORKORDER` / `AGENTHANDLER` / `FINISHED`; Supervisor streams `LAUNCHREQUEST` / `KILLREQUEST` / `NODEEXIT`; voice chain `CODEQ:*` / `VOICE:*` / `CLOUD*` | `REDIS_DB_EPHEMERAL` |
| **1** (persistent) | `GBD:SUPERVISOR:SINGLETON`, `GBD:SUPERVISOR:ALIVE`, `RUNNINGNODES:<unique_id>`, `CODESCOPE:SESSION:<id>`, `CLOUDRUN:<agent_id>`, `CLOUDDRAFT:<order_id>` | `REDIS_DB_PERSISTENT` |

## Encoding

- All field values are **strings** (Redis hash/stream convention).
- Clients use `decode_responses=True`.
- Booleans on the wire are `"true"` / `"false"` (see `bool_field` in `megadesk_contracts/wire/_fields.py`).
- Empty string `""` is used for unused optional fields (e.g. `wt` when `new_wt=true`, Supervisor `parameters`).

## Package index

| Package | Redis type | Key / pattern | DB | Doc |
|---------|------------|---------------|----|-----|
| `WORKORDER` | stream | `WORKORDER` | 0 | [machine-factory-pipeline.md](machine-factory-pipeline.md#workorder) |
| `AGENTHANDLER` | hash | `AGENTHANDLER:<GUID>` | 0 | [machine-factory-pipeline.md](machine-factory-pipeline.md#agenthandlerguid) |
| `FINISHED` | stream | `FINISHED:<REPO>` | 0 | [machine-factory-pipeline.md](machine-factory-pipeline.md#finishedrepo) |
| `LAUNCHREQUEST` | stream | `LAUNCHREQUEST` | 0 | [supervisor.md](supervisor.md#launchrequest) |
| `KILLREQUEST` | stream | `KILLREQUEST` | 0 | [supervisor.md](supervisor.md#killrequest) |
| `NODEEXIT` | stream | `NODEEXIT` | 0 | [supervisor.md](supervisor.md#nodeexit) |
| `RUNNINGNODES` | hash | `RUNNINGNODES:<unique_id>` | 1 | [supervisor.md](supervisor.md#runningnodesunique_id) |
| Supervisor singleton | string | `GBD:SUPERVISOR:SINGLETON` | 1 | [supervisor.md](supervisor.md#gbdsupervisorsingleton) |
| Supervisor alive | string (TTL) | `GBD:SUPERVISOR:ALIVE` | 1 | [supervisor.md](supervisor.md#gbdsupervisoralive) |
| `CODEQ:ASK` | stream | `CODEQ:ASK` | 0 | [voice-chain.md](voice-chain.md#codeqask) |
| `CODEQ:ANSWER` | stream | `CODEQ:ANSWER` | 0 | [voice-chain.md](voice-chain.md#codeqanswer) |
| `VOICE:CONTROL` | stream | `VOICE:CONTROL` | 0 | [voice-chain.md](voice-chain.md#voicecontrol) |
| `VOICE:EVENT` | stream | `VOICE:EVENT` | 0 | [voice-chain.md](voice-chain.md#voiceevent) |
| `CLOUDORDER` | stream | `CLOUDORDER` | 0 | [voice-chain.md](voice-chain.md#cloudorder) |
| `CLOUDFINISHED` | stream | `CLOUDFINISHED` | 0 | [voice-chain.md](voice-chain.md#cloudfinished) |
| CodeScope session | hash | `CODESCOPE:SESSION:<id>` | 1 | [voice-chain.md](voice-chain.md#hashes-db-1) |
| Cloud run | hash | `CLOUDRUN:<agent_id>` | 1 | [voice-chain.md](voice-chain.md#hashes-db-1) |
| Cloud draft | hash | `CLOUDDRAFT:<order_id>` | 1 | [voice-chain.md](voice-chain.md#hashes-db-1) |

## Code references

Every package above is defined exactly once, and every writer imports it from
there. A node shipping its own `redis_packets.py` is a bug, not a shortcut:

- `MegaDesk-contracts/megadesk_contracts/wire/factory.py` — status vocabulary shared by both factories
- `MegaDesk-contracts/megadesk_contracts/wire/machine.py` — `WORKORDER`, `AGENTHANDLER`, `FINISHED`
- `MegaDesk-contracts/megadesk_contracts/wire/cloud.py` — `CLOUDORDER`, `CLOUDFINISHED`, `CLOUDRUN`, `CLOUDDRAFT`
- `MegaDesk-contracts/megadesk_contracts/wire/code_scope.py`
- `MegaDesk-contracts/megadesk_contracts/wire/voice.py`

Supervisor keys/streams (Canvas-owned BE):

- `MegaDesk-Canvas/supervisor/redis_provision.py`
- `MegaDesk-Canvas/supervisor/stream_server.py`
- `MegaDesk-Canvas/supervisor/engine.py`
- `MegaDesk-contracts/megadesk_contracts/supervisor_client.py`

## Obsolete names (do not use)

Older prompts mentioned `WORKREQUEST` and `MERGEREQUEST:*`. The live contract is **`WORKORDER`** and **`FINISHED:<REPO>`** only.

Older names **Plant** / **PlantManager** / **LiveHarness** and Redis hash **`LIVEHARNESS:<GUID>`** (consumer group `plant`) are replaced by **MachineFactory** / **MachineFactoryManager** / **AgentHandler** and **`AGENTHANDLER:<GUID>`** (consumer group `machine_factory`).

Older Supervisor docs mentioned YAML manifests (`register_manifest` / `execute_manifest` / `PARAMETERS_*`), Pub/Sub `launch_node` / `stop_node` / `acknowledgements` / `KILLALL` / `GBD:COMMANDER:ALIVE`, and a Catalog node under `Nodes/Supervisor/`. The live contract is Canvas-owned Supervisor (`python -m supervisor`) with **`LAUNCHREQUEST`** / **`KILLREQUEST`** / **`NODEEXIT`** on DB 0 and **`RUNNINGNODES:<unique_id>`** / **`GBD:SUPERVISOR:SINGLETON`** / **`GBD:SUPERVISOR:ALIVE`** on DB 1.
