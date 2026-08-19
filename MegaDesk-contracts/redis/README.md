# Redis conventions

MegaDesk processes communicate over a shared local Redis. Every process occupies a
**pair** of databases — ephemeral streams and persistent hashes — selected by
``resolve_redis_pair()`` from ``REDIS_URL``. The live pair is always ``(0, 1)``.
Pointing ``REDIS_URL`` at db 4 moves the whole pipeline to ``(4, 5)``; db 0 or 1
in the URL stays on the live pair.

Three families share that pair:

1. **MachineFactory pipeline** (streams + short-lived hashes on **ephemeral**) — TicketDispatcher, MachineFactory, MergeManager
2. **Supervisor** (streams on **ephemeral**; RUNNINGNODES / singleton / alive on **persistent**) — Canvas-owned Supervisor BE and launched BE nodes
3. **Voice chain** (streams on **ephemeral**; session / run / draft hashes on **persistent**) — CodeScope, VoiceDeck, CloudFactory

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
| Pair helper | `resolve_redis_pair()` — live `(0, 1)`; otherwise even `N` → `(N, N+1)` |
| DB selection | Same URL; pass `db=resolve_ephemeral_db(url)` / `resolve_persistent_db(url)` to `Redis.from_url` |
| Factory IPC (sandbox) | **`MEGADESK_FACTORY_REDIS_URL`** — AgentHandler's bus; sandbox `REDIS_URL` is the agent's own pair |
| Docker → host | `REDIS_URL_CONTAINER` / container `REDIS_URL`, typically `redis://host.docker.internal:6379/{db}` |

Do **not** hardcode host/port. Prefer `redis_connect(url, db=resolve_ephemeral_db(url))` — redis-py 8 ignores a `db=` keyword when the URL already names a database.

**`DEV_FLUSH_MODE`** — debug convenience, default off. When MegaDesk-Canvas `main()` boots and the env var is truthy (`1` / `true` / `yes` / `on`, case-insensitive), it FLUSHDB's live DB 0 then DB 1 (`flush_live_redis_pair`) **before** `ensure_supervisor_running()`. Windows: `set DEV_FLUSH_MODE=1`. Lanes (`flush_pair`), pytest, `python -m supervisor` alone, and agent sandboxes never flush 0/1.

MachineFactory, TicketDispatcher, MergeManager, `SupervisorClient`, and Supervisor provision all read **`REDIS_URL`**. They **do not** start Redis. The Canvas-owned Supervisor BE may attach to an existing Redis at `REDIS_URL` or (when the URL host is loopback) provision Docker Redis + Redis Insight if none is reachable.

## Databases

Default Redis has indexes 0–15. Live MegaDesk never leaves 0/1. MachineFactory leases the even DBs 2–12 for sandboxed agents that work on MegaDesk; host pytest owns 14/15. Leases (`MEGADESK:LANE:*`) live on **live db 1** and are factory-owned with TTL — agents do not mark them free.

| DB | Use | Constants |
|----|-----|-----------|
| **0** (live ephemeral) | Default realtime traffic: MachineFactory `WORKORDER` / `AGENTHANDLER` / `FINISHED` / `GRAPHRUN` / `GRAPHEVENT`; Supervisor streams `SUPERVISOR:LAUNCHREQUEST` / `SUPERVISOR:KILLREQUEST` / `NODEEXIT`; voice chain `CODEQ:*` / `VOICE:*` / `CLOUD*` | `REDIS_DB_EPHEMERAL` |
| **1** (live persistent) | `SUPERVISOR:SINGLETON`, `SUPERVISOR:ALIVE`, `RUNNINGNODES:<unique_id>`, `CODESCOPE:SESSION:<id>`, `CLOUDRUN:<agent_id>`, `CLOUDDRAFT:<order_id>`, `MEGADESK:LANE:*` | `REDIS_DB_PERSISTENT` |
| **2/3 … 12/13** | Agent lanes (six concurrent). Sandbox `REDIS_URL` names the even half. | `AGENT_LANE_EPHEMERAL_DBS` |
| **14/15** | Host pytest pair. Never allocated to an agent. | `HOST_PYTEST_EPHEMERAL_DB` / `HOST_PYTEST_PERSISTENT_DB` |

## Encoding

- All field values are **strings** (Redis hash/stream convention).
- Clients use `decode_responses=True`.
- Booleans on the wire are `"true"` / `"false"` (see `bool_field` in `megadesk_contracts/wire/_fields.py`).
- Empty string `""` is used for unused optional fields (e.g. `wt` when `new_wt=true`, Supervisor `parameters`).

## Package index

| Package | Redis type | Key / pattern | Live DB | Doc |
|---------|------------|---------------|----|-----|
| `WORKORDER` | stream | `WORKORDER` | 0 | [machine-factory-pipeline.md](machine-factory-pipeline.md#workorder) |
| `AGENTHANDLER` | hash | `AGENTHANDLER:<GUID>` | 0 | [machine-factory-pipeline.md](machine-factory-pipeline.md#agenthandlerguid) |
| `GRAPHRUN` | hash | `GRAPHRUN:<GUID>` | 0 | [work-graph.md](work-graph.md#graphrunguid) |
| `GRAPHEVENT` | stream | `GRAPHEVENT` | 0 | [work-graph.md](work-graph.md#graphevent) |
| `FINISHED` | stream | `FINISHED:<REPO>` | 0 | [machine-factory-pipeline.md](machine-factory-pipeline.md#finishedrepo) |
| `SUPERVISOR:LAUNCHREQUEST` | stream | `SUPERVISOR:LAUNCHREQUEST` | 0 | [supervisor.md](supervisor.md#supervisorlaunchrequest) |
| `SUPERVISOR:KILLREQUEST` | stream | `SUPERVISOR:KILLREQUEST` | 0 | [supervisor.md](supervisor.md#supervisorkillrequest) |
| `NODEEXIT` | stream | `NODEEXIT` | 0 | [supervisor.md](supervisor.md#nodeexit) |
| `RUNNINGNODES` | hash | `RUNNINGNODES:<unique_id>` | 1 | [supervisor.md](supervisor.md#runningnodesunique_id) |
| Supervisor singleton | string | `SUPERVISOR:SINGLETON` | 1 | [supervisor.md](supervisor.md#supervisorsingleton) |
| Supervisor alive | string (TTL) | `SUPERVISOR:ALIVE` | 1 | [supervisor.md](supervisor.md#supervisoralive) |
| `CODEQ:ASK` | stream | `CODEQ:ASK` | 0 | [voice-chain.md](voice-chain.md#codeqask) |
| `CODEQ:ANSWER` | stream | `CODEQ:ANSWER` | 0 | [voice-chain.md](voice-chain.md#codeqanswer) |
| `VOICE:CONTROL` | stream | `VOICE:CONTROL` | 0 | [voice-chain.md](voice-chain.md#voicecontrol) |
| `VOICE:EVENT` | stream | `VOICE:EVENT` | 0 | [voice-chain.md](voice-chain.md#voiceevent) |
| `CLOUDORDER` | stream | `CLOUDORDER` | 0 | [voice-chain.md](voice-chain.md#cloudorder) |
| `CLOUDFINISHED` | stream | `CLOUDFINISHED` | 0 | [voice-chain.md](voice-chain.md#cloudfinished) |
| CodeScope session | hash | `CODESCOPE:SESSION:<id>` | 1 | [voice-chain.md](voice-chain.md#hashes-db-1) |
| Cloud run | hash | `CLOUDRUN:<agent_id>` | 1 | [voice-chain.md](voice-chain.md#hashes-db-1) |
| Cloud draft | hash | `CLOUDDRAFT:<order_id>` | 1 | [voice-chain.md](voice-chain.md#hashes-db-1) |
| Agent lane lease | string (TTL) | `MEGADESK:LANE:<even>` / `MEGADESK:LANEBYRUN:<run_key>` | 1 | MachineFactory allocator; never agent-written |

## Code references

Every package above is defined exactly once, and every writer imports it from
there. A node shipping its own `redis_packets.py` is a bug, not a shortcut:

- `MegaDesk-contracts/megadesk_contracts/wire/factory.py` — status vocabulary shared by both factories
- `MegaDesk-contracts/megadesk_contracts/wire/machine.py` — `WORKORDER`, `AGENTHANDLER`, `FINISHED`
- `MegaDesk-contracts/megadesk_contracts/wire/graph.py` — `GRAPHRUN`, `GRAPHEVENT`, `WORK_GRAPH`
- `MegaDesk-contracts/megadesk_contracts/wire/cloud.py` — `CLOUDORDER`, `CLOUDFINISHED`, `CLOUDRUN`, `CLOUDDRAFT`
- `MegaDesk-contracts/megadesk_contracts/wire/code_scope.py`
- `MegaDesk-contracts/megadesk_contracts/wire/voice.py`

Supervisor keys/streams (Canvas-owned BE):

- `MegaDesk-contracts/megadesk_contracts/supervisor_client.py` — stream names, `RUNNINGNODES`, `SupervisorClient`
- `MegaDesk-Canvas/supervisor/` — the BE that consumes those streams
