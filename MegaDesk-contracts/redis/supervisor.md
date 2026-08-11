# Supervisor Redis packages

Supervisor is **Canvas infrastructure**, not a `MegaDesk.nodes` Catalog entry. It lives under `MegaDesk-Canvas/supervisor/`:

- **BE:** `python -m supervisor` (started on canvas launch via `megadesk_contracts.ensure_supervisor_running()`)
- **FE:** collapsible chrome panel via `supervisor.panel.build_supervisor_panel`

Supervisor uses Redis for:

1. **Streams** launch / kill / exit control plane (**DB 0** ephemeral)
2. **RUNNINGNODES** instance registry hashes (**DB 1** persistent)
3. **Singleton** lock so only one Supervisor BE can run (**DB 1**)
4. **Alive** heartbeat key (**DB 1**)
5. **Per-instance log files** under `MegaDesk-Canvas/logs/` (not Redis)

Node backends are discovered from installed `MegaDesk.nodes` entry points via
`get_exec_spec("BE")` → `BeSpec` (argv + optional cwd). There are no YAML
manifests. Launch `parameters` are present on the wire but currently always `""`.

This family is independent of the MissionControl pipeline streams, but shares the same
localhost Redis server (different DB indexes).

There is **no** request/response ack path. Producers `XADD` and move on;
clients observe `RUNNINGNODES:<unique_id>` (or process state) if they need
confirmation. Log **bytes** live in files; Redis carries metadata and exit events
only.

Constants live in `megadesk_contracts.supervisor_client`:
`REDIS_DB_EPHEMERAL=0`, `REDIS_DB_PERSISTENT=1`,
`SUPERVISOR_SINGLETON_KEY`, `SUPERVISOR_ALIVE_KEY`.

---

## Databases

| DB | Role |
|----|------|
| `0` (ephemeral) | `LAUNCHREQUEST`, `KILLREQUEST`, `NODEEXIT`; MissionControl pipeline traffic |
| `1` (persistent) | `GBD:SUPERVISOR:SINGLETON`, `GBD:SUPERVISOR:ALIVE`, `RUNNINGNODES:<unique_id>` |

Launch contract for BE nodes:

```text
subprocess.Popen(
  BeSpec.argv,
  cwd=BeSpec.cwd,
  stdout=log_file,
  stderr=STDOUT,
  env={…, MEGADESK_UNIQUE_ID, MEGADESK_NODE, MEGADESK_LOG_PATH},
)
```

Each launch gets a global `unique_id` (UUID4). Multiple instances of the same
`node_endpoint` may run concurrently.

Log path convention: `MegaDesk-Canvas/logs/<node_endpoint>/<unique_id>.log`
(absolute path stored on the `RUNNINGNODES` hash as `log_path`).
Supervisor BE self-log: `MegaDesk-Canvas/logs/supervisor/supervisor.log`.

---

## Bootstrap

Canvas startup (`MegaDesk-Canvas/main.py`) calls
`megadesk_contracts.ensure_supervisor_running()`, which runs
`python -m supervisor` from `MegaDesk-Canvas/` and waits for
`GBD:SUPERVISOR:ALIVE` on DB 1 (default timeout 12s). The BE is **not** launched
via `LAUNCHREQUEST` and is **not** a Catalog / FeSpec drop.

Redis provision (prefer existing `localhost:6379`, else Docker `gbd-redis` +
optional Insight on `5540`) happens inside the Supervisor BE — see
`MegaDesk-Canvas/supervisor/redis_provision.py`.

---

## LAUNCHREQUEST

| Property | Value |
|----------|-------|
| Type | Stream |
| Database | **0** |
| Key | `LAUNCHREQUEST` |
| Consumer group | `supervisor` |
| Primary consumer | Supervisor BE (`python -m supervisor`) |
| Producers | Supervisor panel, MegaDesk canvas (FE drop with BE), manual `XADD` |

### Fields

| Field | Required | Notes |
|-------|----------|-------|
| `node_endpoint` | yes | `BeSpec.name` from `MegaDesk.nodes` discovery |
| `parameters` | yes | Currently always `""` |

### On consume

1. Assign `unique_id` (UUID4)
2. Resolve `node_endpoint` via `discover_backends()` / `get_backend`
3. Open `logs/<node_endpoint>/<unique_id>.log`; `Popen` with stdout/stderr redirected and `MEGADESK_*` env
4. `HSET RUNNINGNODES:<unique_id>` (DB 1) with identity, PID, `status=running`, `log_path`, `launched_at`

### Example

```text
XADD LAUNCHREQUEST * node_endpoint mission_control parameters ""
```

---

## KILLREQUEST

| Property | Value |
|----------|-------|
| Type | Stream |
| Database | **0** |
| Key | `KILLREQUEST` |
| Consumer group | `supervisor` |
| Primary consumer | Supervisor BE |
| Producers | Supervisor panel, tools, manual `XADD` |

### Fields

| Field | Required | Notes |
|-------|----------|-------|
| `node_endpoint` | yes | Must match the running instance |
| `unique_id` | yes | Targets one `RUNNINGNODES:<unique_id>` entry |

### On consume

1. Look up managed process by `unique_id` (or Redis-only stale/exited hash)
2. Verify `node_endpoint` matches
3. Graceful → force shutdown if still alive
4. `DEL RUNNINGNODES:<unique_id>` on DB 1 (log file is left on disk)

### Example

```text
XADD KILLREQUEST * node_endpoint mission_control unique_id 3f2a9c1e-…
```

---

## RUNNINGNODES:\<unique_id\>

| Property | Value |
|----------|-------|
| Type | Hash |
| Database | **1** |
| Key | `RUNNINGNODES:<unique_id>` |
| Writer | Supervisor BE on launch and on reaped exit |
| Readers | Supervisor panel (running list), operators |

### Fields

| Field | Notes |
|-------|-------|
| `node_endpoint` | `BeSpec.name` |
| `unique_id` | Same as key suffix |
| `parameters` | Launch parameters (currently `""`) |
| `PID` | OS process id as a string |
| `status` | `running` or `exited` |
| `log_path` | Absolute path to the instance log file |
| `launched_at` | ISO-8601 UTC timestamp |
| `exit_code` | Process exit code as string; empty while `running` |
| `exited_at` | ISO-8601 UTC timestamp; empty while `running` |

Natural process death does **not** delete the hash. The Supervisor reaper sets
`status=exited`, `exit_code`, and `exited_at`, publishes `NODEEXIT`, and drops
the in-memory handle. The hash remains until an operator `KILLREQUEST` (Stop)
clears it. Intentional kill always `DEL`s the hash.

### Example

```text
HSET RUNNINGNODES:3f2a9c1e-… node_endpoint mission_control unique_id 3f2a9c1e-…
  parameters "" PID 12345 status running
  log_path C:/…/MegaDesk-Canvas/logs/mission_control/3f2a9c1e-….log
  launched_at 2026-08-06T20:00:00+00:00 exit_code "" exited_at ""
```

---

## NODEEXIT

| Property | Value |
|----------|-------|
| Type | Stream |
| Database | **0** |
| Key | `NODEEXIT` |
| Writer | Supervisor BE reaper (natural exit only) |
| Readers | Operators / future attach points (alerts, auto-restart) |

Metadata only — **never** log line bodies.

### Fields

| Field | Required | Notes |
|-------|----------|-------|
| `unique_id` | yes | |
| `node_endpoint` | yes | |
| `exit_code` | yes | Integer as string |
| `log_path` | yes | Absolute path |
| `exited_at` | yes | ISO-8601 UTC |

### Example

```text
XADD NODEEXIT * unique_id 3f2a9c1e-… node_endpoint mission_control
  exit_code 1 log_path C:/…/MegaDesk-Canvas/logs/mission_control/3f2a9c1e-….log
  exited_at 2026-08-06T20:01:00+00:00
```

---

## GBD:SUPERVISOR:SINGLETON

| Property | Value |
|----------|-------|
| Type | String |
| Database | **1** |
| Key | `GBD:SUPERVISOR:SINGLETON` (`SUPERVISOR_SINGLETON_KEY`) |
| Value | Owner token (typically Supervisor BE PID as string) |

Acquired when the Supervisor BE starts; a second BE exits if the lock is already held.
Released on clean shutdown.

---

## GBD:SUPERVISOR:ALIVE

| Property | Value |
|----------|-------|
| Type | String |
| Database | **1** |
| Key | `GBD:SUPERVISOR:ALIVE` (`SUPERVISOR_ALIVE_KEY`) |
| Value | `"1"` |
| TTL | 5 seconds (refreshed by Supervisor BE heartbeat) |

Presence of the key means the Supervisor BE process is reachable. Cleared on
shutdown. `ensure_supervisor_running()` / `SupervisorClient.backend_ok()` observe
this key on DB 1.

---

## Connection / provision

| Setting | Convention |
|---------|------------|
| Host / port | `localhost:6379` |
| Prefer | Attach to existing Redis |
| Else | Docker container `gbd-redis` (`redis:7`) + optional `gbd-redis-insight` on port `5540` |

See `MegaDesk-Canvas/supervisor/redis_provision.py`.

---

## Obsolete names (do not use)

| Old | Replacement |
|-----|-------------|
| `Nodes/Supervisor/` Catalog node | Canvas-owned `MegaDesk-Canvas/supervisor/` |
| `python -m backend` | `python -m supervisor` |
| `supervisor_frontend` | `supervisor.panel.build_supervisor_panel` |
| Drop-supervisor FE bootstrap | Canvas startup `ensure_supervisor_running()` |
| `launch_node:<identity>` Pub/Sub | `LAUNCHREQUEST` stream (DB 0) |
| `stop_node:<identity>` Pub/Sub | `KILLREQUEST` stream (DB 0) |
| `acknowledgements:<identity>` | removed (no ack path) |
| `KILLALL` Pub/Sub | `KILLREQUEST` per instance (or panel stop-all) |
| `GBD:COMMANDER:ALIVE` | `GBD:SUPERVISOR:ALIVE` (DB 1) |
| YAML manifests / `PARAMETERS_*` | removed |
| Alive / RUNNINGNODES on DB 0 | DB 1 |
