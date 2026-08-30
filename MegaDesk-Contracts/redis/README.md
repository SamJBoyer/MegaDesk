# Redis conventions

MegaDesk processes communicate over a shared local Redis. Every process occupies a
**pair** of databases — ephemeral streams and persistent hashes — selected by
``resolve_redis_pair()`` from ``REDIS_URL``. The live pair is always ``(0, 1)``.
Pointing ``REDIS_URL`` at db 4 moves the whole pipeline to ``(4, 5)``; db 0 or 1
in the URL stays on the live pair.

Three families share that pair:

1. **MachineFactory pipeline** (pub/sub order signal, reference streams + short-lived hashes on **ephemeral**) — WorkDispatcher, MachineFactory
2. **Supervisor** (streams on **ephemeral**; RUNNINGNODES / singleton / alive on **persistent**) — Canvas-owned Supervisor BE and launched BE nodes
3. **Voice chain** (streams on **ephemeral**; session / run hashes on **persistent**) — CodeScope, VoiceDeck, CloudFactory, Notepad

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
| Factory IPC (sandbox) | **`MEGADESK_FACTORY_REDIS_URL`** — AgentHandler's bus on the host pair; sandbox `REDIS_URL` is a Redis **sidecar** for agent MegaDesk |
| Docker → host | `REDIS_URL_CONTAINER` / container `REDIS_URL`, typically `redis://host.docker.internal:6379/{db}` |

Do **not** hardcode host/port. Prefer `redis_connect(url, db=resolve_ephemeral_db(url))` — redis-py 8 ignores a `db=` keyword when the URL already names a database.

**`DEV_FLUSH_MODE`** — debug convenience, default on. When MegaDesk-Canvas `main()` boots, unless the env var is explicitly falsey (`0` / `false` / `no` / `off`, case-insensitive), it FLUSHDB's live DB 0 then DB 1 (`flush_live_redis_pair`) **before** `ensure_supervisor_running()`. Disable with `set DEV_FLUSH_MODE=0` (Windows) or `export DEV_FLUSH_MODE=0`. Pytest, `python -m supervisor` alone, and agent sandboxes never flush 0/1.

MachineFactory, WorkDispatcher, `SupervisorClient`, and Supervisor provision all read **`REDIS_URL`**. They **do not** start Redis. The Canvas-owned Supervisor BE may attach to an existing Redis at `REDIS_URL` or (when the URL host is loopback) provision Docker Redis if none is reachable.

**Bind.** Auto-provision publishes Redis as `127.0.0.1:{port}:6379`, not `{port}:6379`. Redis Insight is **opt-in** (`MEGADESK_REDIS_INSIGHT=1`) and, when started, publishes `127.0.0.1:5540:5540`. Neither service is published on `0.0.0.0`. Do not add `--requirepass` to an operator Redis that is already running. When **creating** a new container, `REDIS_PASSWORD` (if set) is passed as `--requirepass`; `REDIS_URL` must then include that password (`redis://:password@localhost:6379/0`). Existing URL resolution goes through `Redis.from_url` — do not hardcode host/port.

**Factory ACL.** A MachineFactory sandbox talks to the host pair as Redis user `megadesk-factory` (`megadesk_contracts.FACTORY_ACL_USER`). That user may use factory keys only (`WORKORDER`, `FINISHED:*`, `AGENTHANDLER:*`, `GRAPHRUN:*`, `GRAPHEVENT`) and is denied `FLUSHDB` / `FLUSHALL` / `CONFIG` / `ACL` / `DEBUG` / `MODULE` / `SCRIPT` plus Supervisor keys (`SUPERVISOR:*`, `NODEEXIT`, `NODE:SHUTDOWN*`, `RUNNINGNODES:*`, `NODEHB:*`). The factory manager applies `ACL SETUSER` as admin before launch. If ACL cannot be applied, sandbox launch fails rather than injecting an unauthenticated URL. Host canvas / supervisor / MachineFactory **manager** keep the default/admin user.

One-time / operator Redis: run as admin (or let the factory manager do it):

```text
ACL SETUSER megadesk-factory reset on ><password> resetkeys ~WORKORDER ~FINISHED:* ~AGENTHANDLER:* ~GRAPHRUN:* ~GRAPHEVENT resetchannels -@all +@read +@write +@stream +@hash +@connection -@admin -@dangerous -FLUSHDB -FLUSHALL -CONFIG -ACL -DEBUG -MODULE -SCRIPT
```

`MEGADESK_FACTORY_REDIS_PASSWORD` pins the password; otherwise the manager generates one for the process. The sandbox URL is `redis://megadesk-factory:<password>@host.docker.internal:6379/{db}`.

## Databases

Default Redis has indexes 0–15. Live MegaDesk never leaves 0/1. MachineFactory sandboxes get their own Redis **sidecar** (injected as sandbox `REDIS_URL`) and talk to the factory bus via `MEGADESK_FACTORY_REDIS_URL` on the host pair. Host pytest owns 14/15.

| DB | Use | Constants |
|----|-----|-----------|
| **0** (live ephemeral) | Default realtime traffic: MachineFactory `WORKORDER` / `AGENTHANDLER` / `FINISHED` / `GRAPHRUN` / `GRAPHEVENT`; Supervisor streams `SUPERVISOR:LAUNCHREQUEST` / `SUPERVISOR:KILLREQUEST` / `NODEEXIT`; voice chain `CODEQ:*` / `VOICE:*` / `CLOUD*`; PromptImprover `SARGENT:*` | `REDIS_DB_EPHEMERAL` |
| **1** (live persistent) | `SUPERVISOR:SINGLETON`, `SUPERVISOR:ALIVE`, `RUNNINGNODES:<unique_id>`, `CODESCOPE:SESSION:<id>`, `CLOUDRUN:<agent_id>` | `REDIS_DB_PERSISTENT` |
| **14/15** | Host pytest pair. Never handed to an agent. | `HOST_PYTEST_EPHEMERAL_DB` / `HOST_PYTEST_PERSISTENT_DB` |

## Encoding

- All field values are **strings** (Redis hash/stream convention).
- Clients use `decode_responses=True`.
- Booleans on the wire are `"true"` / `"false"` (see `bool_field` in `megadesk_contracts/wire/_fields.py`).
- Empty string `""` is used for unused optional fields (e.g. `pr_url` on error paths, Supervisor `parameters`).

## Package index

| Package | Redis type | Key / pattern | Live DB | Doc |
|---------|------------|---------------|----|-----|
| `WORKORDER` | pub/sub + stream | `WORKORDER` | 0 | [machine-factory-pipeline.md](machine-factory-pipeline.md#workorder-channel) |
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
| `NOTEPAD:CMD` | stream | `NOTEPAD:CMD` | 0 | [notepad.md](notepad.md#notepadcmd) |
| `CLOUDORDER` | pub/sub + stream | `CLOUDORDER` | 0 | [voice-chain.md](voice-chain.md#cloudorder) |
| `CLOUDFINISHED` | stream | `CLOUDFINISHED` | 0 | [voice-chain.md](voice-chain.md#cloudfinished) |
| CodeScope session | hash | `CODESCOPE:SESSION:<id>` | 1 | [voice-chain.md](voice-chain.md#hashes-db-1) |
| Cloud run | hash | `CLOUDRUN:<agent_id>` | 1 | [voice-chain.md](voice-chain.md#hashes-db-1) |
| `SARGENT:ASK` | stream | `SARGENT:ASK` | 0 | `megadesk_contracts.wire.sargent` |
| `SARGENT:ANSWER` | stream | `SARGENT:ANSWER` | 0 | `megadesk_contracts.wire.sargent` |

## Code references

Every package above is defined exactly once, and every writer imports it from
there. A node shipping its own `redis_packets.py` is a bug, not a shortcut:

- `MegaDesk-Contracts/megadesk_contracts/wire/factory.py` — status vocabulary shared by both factories
- `MegaDesk-Contracts/megadesk_contracts/wire/machine.py` — `WORKORDER`, `AGENTHANDLER`, `FINISHED`
- `MegaDesk-Contracts/megadesk_contracts/wire/graph.py` — `GRAPHRUN`, `GRAPHEVENT`, `WORK_GRAPH`, `MASSIVE_PROJECT_GRAPH`
- `MegaDesk-Contracts/megadesk_contracts/wire/cloud.py` — `CLOUDORDER`, `CLOUDFINISHED`, `CLOUDRUN`
- `MegaDesk-Contracts/megadesk_contracts/wire/code_scope.py`
- `MegaDesk-Contracts/megadesk_contracts/wire/sargent.py`
- `MegaDesk-Contracts/megadesk_contracts/wire/voice.py`
- `MegaDesk-Contracts/megadesk_contracts/wire/notepad.py`

Supervisor keys/streams (Canvas-owned BE):

- `MegaDesk-Contracts/megadesk_contracts/supervisor_client.py` — stream names, `RUNNINGNODES`, `SupervisorClient`
- `MegaDesk-Canvas/supervisor/` — the BE that consumes those streams
