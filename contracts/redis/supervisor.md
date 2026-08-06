# Supervisor Redis packages

Supervisor uses Redis for:

1. **Streams** launch / kill / exit control plane (DB 0)
2. **RUNNINGNODES** instance registry hashes (DB 0)
3. **Supervisor alive** heartbeat key (DB 0)
4. **Per-instance log files** under `Nodes/Supervisor/logs/` (not Redis)

Node backends are discovered from installed `MegaDesk.nodes` entry points via
`get_exec_spec("BE")` → `BeSpec` (argv + optional cwd). There are no YAML
manifests. Launch `parameters` are present on the wire but currently always `""`.

This family is independent of the Plant pipeline streams, but shares the same
localhost Redis server.

There is **no** request/response ack path. Producers `XADD` and move on;
clients observe `RUNNINGNODES:<unique_id>` (or process state) if they need
confirmation. Log **bytes** live in files; Redis carries metadata and exit events
only.

---

## Databases

| DB | Role |
|----|------|
| `0` | Streams, `RUNNINGNODES:*` hashes, `GBD:SUPERVISOR:ALIVE` |

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

Log path convention: `Nodes/Supervisor/logs/<node_endpoint>/<unique_id>.log`
(absolute path stored on the `RUNNINGNODES` hash as `log_path`).

---

## LAUNCHREQUEST

| Property | Value |
|----------|-------|
| Type | Stream |
| Key | `LAUNCHREQUEST` |
| Consumer group | `supervisor` |
| Primary consumer | Supervisor BE (`python -m backend`) |
| Producers | Supervisor FE, MegaDesk canvas, manual `XADD` |

### Fields

| Field | Required | Notes |
|-------|----------|-------|
| `node_endpoint` | yes | `BeSpec.name` from `MegaDesk.nodes` discovery |
| `parameters` | yes | Currently always `""` |

### On consume

1. Assign `unique_id` (UUID4)
2. Resolve `node_endpoint` via `discover_backends()` / `get_backend`
3. Open `logs/<node_endpoint>/<unique_id>.log`; `Popen` with stdout/stderr redirected and `MEGADESK_*` env
4. `HSET RUNNINGNODES:<unique_id>` with identity, PID, `status=running`, `log_path`, `launched_at`

The Supervisor BeSpec itself is never launched via this stream (bootstrap uses
`megadesk.ensure_supervisor_running()`).

### Example

```text
XADD LAUNCHREQUEST * node_endpoint plant parameters ""
```

---

## KILLREQUEST

| Property | Value |
|----------|-------|
| Type | Stream |
| Key | `KILLREQUEST` |
| Consumer group | `supervisor` |
| Primary consumer | Supervisor BE |
| Producers | Supervisor FE, tools, manual `XADD` |

### Fields

| Field | Required | Notes |
|-------|----------|-------|
| `node_endpoint` | yes | Must match the running instance |
| `unique_id` | yes | Targets one `RUNNINGNODES:<unique_id>` entry |

### On consume

1. Look up managed process by `unique_id` (or Redis-only stale/exited hash)
2. Verify `node_endpoint` matches
3. Graceful → force shutdown if still alive
4. `DEL RUNNINGNODES:<unique_id>` (log file is left on disk)

### Example

```text
XADD KILLREQUEST * node_endpoint plant unique_id 3f2a9c1e-…
```

---

## RUNNINGNODES:\<unique_id\>

| Property | Value |
|----------|-------|
| Type | Hash |
| Key | `RUNNINGNODES:<unique_id>` |
| Writer | Supervisor BE on launch and on reaped exit |
| Readers | Supervisor FE (running list), operators |

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
HSET RUNNINGNODES:3f2a9c1e-… node_endpoint plant unique_id 3f2a9c1e-…
  parameters "" PID 12345 status running
  log_path C:/…/Nodes/Supervisor/logs/plant/3f2a9c1e-….log
  launched_at 2026-08-06T20:00:00+00:00 exit_code "" exited_at ""
```

---

## NODEEXIT

| Property | Value |
|----------|-------|
| Type | Stream |
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
XADD NODEEXIT * unique_id 3f2a9c1e-… node_endpoint plant
  exit_code 1 log_path C:/…/logs/plant/3f2a9c1e-….log
  exited_at 2026-08-06T20:01:00+00:00
```

---

## GBD:SUPERVISOR:ALIVE

| Property | Value |
|----------|-------|
| Type | String |
| Database | **0** |
| Key | `GBD:SUPERVISOR:ALIVE` |
| Value | `"1"` |
| TTL | 5 seconds (refreshed by Supervisor BE heartbeat) |

Presence of the key means the Supervisor BE process is reachable. Cleared on
shutdown.

**Supervisor bootstrap:** dropping the `supervisor` FE does **not** use
`LAUNCHREQUEST` (the BE is not up yet). MegaDesk / the FE call
`megadesk.ensure_supervisor_running()`, which spawns the Supervisor `BeSpec`
(`python -m backend`) directly and waits for `GBD:SUPERVISOR:ALIVE`.

---

## Connection / provision

| Setting | Convention |
|---------|------------|
| Host / port | `localhost:6379` |
| Prefer | Attach to existing Redis |
| Else | Docker container `gbd-redis` (`redis:7`) + optional `gbd-redis-insight` on port `5540` |

See `Nodes/Supervisor/backend/redis_provision.py`.

---

## Obsolete names (do not use)

| Old | Replacement |
|-----|-------------|
| `launch_node:<identity>` Pub/Sub | `LAUNCHREQUEST` stream |
| `stop_node:<identity>` Pub/Sub | `KILLREQUEST` stream |
| `acknowledgements:<identity>` | removed (no ack path) |
| `KILLALL` Pub/Sub | `KILLREQUEST` per instance (or FE stop-all) |
| `GBD:COMMANDER:ALIVE` | `GBD:SUPERVISOR:ALIVE` |
| YAML manifests / `PARAMETERS_*` | removed |
