# Supervisor Redis packages

Supervisor uses Redis for:

1. **Streams** launch / kill control plane (DB 0)
2. **RUNNINGNODES** instance registry hashes (DB 0)
3. **Supervisor alive** heartbeat key (DB 0)

Node backends are discovered from installed `MegaDesk.nodes` entry points via
`get_exec_spec("BE")` → `BeSpec` (argv + optional cwd). There are no YAML
manifests. Launch `parameters` are present on the wire but currently always `""`.

This family is independent of the Plant pipeline streams, but shares the same
localhost Redis server.

There is **no** request/response ack path. Producers `XADD` and move on;
clients observe `RUNNINGNODES:<unique_id>` (or process state) if they need
confirmation.

---

## Databases

| DB | Role |
|----|------|
| `0` | Streams, `RUNNINGNODES:*` hashes, `GBD:SUPERVISOR:ALIVE` |

Launch contract for BE nodes:

```text
subprocess.Popen(BeSpec.argv, cwd=BeSpec.cwd)
```

Each launch gets a global `unique_id` (UUID4). Multiple instances of the same
`node_endpoint` may run concurrently.

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
3. `Popen(BeSpec.argv, cwd=BeSpec.cwd)`
4. `HSET RUNNINGNODES:<unique_id>` with `node_endpoint`, `unique_id`, `parameters`, `PID`

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

1. Look up managed process by `unique_id`
2. Verify `node_endpoint` matches
3. Graceful → force shutdown
4. `DEL RUNNINGNODES:<unique_id>`

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
| Writer | Supervisor BE on launch |
| Readers | Supervisor FE (running list), operators |

### Fields

| Field | Notes |
|-------|-------|
| `node_endpoint` | `BeSpec.name` |
| `unique_id` | Same as key suffix |
| `parameters` | Launch parameters (currently `""`) |
| `PID` | OS process id as a string |

### Example

```text
HSET RUNNINGNODES:3f2a9c1e-… node_endpoint plant unique_id 3f2a9c1e-… parameters "" PID 12345
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
