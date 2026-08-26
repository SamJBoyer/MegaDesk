# Supervisor Redis packages

Supervisor is **Canvas infrastructure**, not a `MegaDesk.nodes` Catalog entry. It lives under `MegaDesk-Canvas/supervisor/`:

- **BE:** `python -m supervisor` (started on canvas launch via `megadesk_contracts.ensure_supervisor_running()`)
- **FE:** right-hand collapsible chrome pane (Nodes / Logs tabs) via `supervisor.panel.build_supervisor_panel`

Supervisor uses Redis for:

1. **Streams** launch / kill / exit control plane (**DB 0** ephemeral)
2. **RUNNINGNODES** instance registry hashes (**DB 1** persistent)
3. **Singleton** lock so only one Supervisor BE can run (**DB 1**)
4. **Alive** heartbeat key (**DB 1**)
5. **Per-node session files** under worktree `Logs/{session}/` (not Redis)

Node backends are discovered from installed `MegaDesk.nodes` entry points via
`get_be_spec()` → `BeSpec` (argv + optional cwd). There are no YAML
manifests. Launch `parameters` are a JSON object of the graph kvps the FE asked this BE to
start with, or `""` when the node declared none. The BE reads them back with
`megadesk_contracts.parameters_from_env()` (`MEGADESK_PARAMETERS`).

Each BE installs `megadesk_contracts.NodeRuntime`, which writes `NODEHB:<unique_id>`
on DB 1 every 5s (`pid`, `status`, `node`) and exits if `NODE:SHUTDOWN` or
`NODE:SHUTDOWN:<unique_id>` is `1`, or if Redis is unreachable. A present
`NODEHB` hash is the running-node signal (Redis TTL); OS pid checks are only
used during the post-launch grace window. Dead hashes are deleted, not shown
as exited. The operator panel lists **running nodes** only.

This family is independent of the MachineFactory pipeline streams, but shares the same
Redis server via **`REDIS_URL`** (different DB indexes).

There is **no** request/response ack path. Producers `XADD` and move on;
clients observe `RUNNINGNODES:<unique_id>` (or process state) if they need
confirmation. Log **bytes** live in files; Redis carries metadata and exit events
only.

Constants live in `megadesk_contracts.supervisor_client`:
`DEFAULT_REDIS_URL`, `resolve_redis_url()`, `resolve_redis_pair()`, live defaults
`REDIS_DB_EPHEMERAL=0`, `REDIS_DB_PERSISTENT=1`,
`SUPERVISOR_SINGLETON_KEY`, `SUPERVISOR_ALIVE_KEY`.

---

## Databases

| DB | Role |
|----|------|
| live `0` (ephemeral) | `SUPERVISOR:LAUNCHREQUEST`, `SUPERVISOR:KILLREQUEST`, `NODEEXIT`; MachineFactory pipeline traffic |
| live `1` (persistent) | `SUPERVISOR:SINGLETON`, `SUPERVISOR:ALIVE`, `RUNNINGNODES:<unique_id>`, `NODEHB:<unique_id>`, `NODE:SHUTDOWN` |

A process whose `REDIS_URL` names another even DB uses that index and the next one
instead (`resolve_redis_pair`). Live MegaDesk stays on 0/1.

Launch contract for BE nodes:

```text
subprocess.Popen(
  BeSpec.argv,
  cwd=BeSpec.cwd,
  stdout=log_file,
  stderr=STDOUT,
  env={…, MEGADESK_UNIQUE_ID, MEGADESK_NODE, MEGADESK_LOG_PATH, MEGADESK_PARAMETERS},
)
```

Each launch gets a global `unique_id` (UUID4). Multiple instances of the same
`node_endpoint` may run concurrently; they append to the same session file.

Log path convention: `<worktree>/Logs/<session>/<node_endpoint>.md`
resolved from `MEGADESK_LOGS_DIR` (the live session folder), else
`Logs/CURRENT`, with `Logs/` itself from `MEGADESK_LOGS_ROOT` or the parent of
`MEGADESK_CANVAS_ROOT`. Session identity is a Supervisor generation — canvas
open does not rotate or move files.
Supervisor BE self-log: `<worktree>/Logs/<session>/supervisor.md`.
Canvas process: `<worktree>/Logs/<session>/canvas.md`.

---

## Bootstrap

Canvas startup (`MegaDesk-Canvas/main.py`) optionally FLUSHDB's live DB 0 then
DB 1 when `DEV_FLUSH_MODE` is on (`1` / `true` / `yes` / `on`; default off),
then calls `megadesk_contracts.ensure_supervisor_running()`, which runs
`python -m supervisor` from `MegaDesk-Canvas/` and waits for
`SUPERVISOR:ALIVE` on DB 1 (default timeout 12s). Flush first so the new
supervisor recreates consumer groups, `SUPERVISOR:ALIVE`, and
`SUPERVISOR:SINGLETON` on empty DBs. `python -m supervisor` alone does not
flush. The BE is **not** launched via `SUPERVISOR:LAUNCHREQUEST` and is
**not** a Catalog / FeSpec drop.

Redis provision (prefer existing server at `REDIS_URL`, else Docker `megadesk-redis` +
optional Insight on `5540` when the URL host is loopback) happens inside the
Supervisor BE — see `MegaDesk-Canvas/supervisor/redis_provision.py`.

---

## SUPERVISOR:LAUNCHREQUEST

| Property | Value |
|----------|-------|
| Type | Stream |
| Database | **0** |
| Key | `SUPERVISOR:LAUNCHREQUEST` |
| Consumer group | `supervisor` |
| Primary consumer | Supervisor BE (`python -m supervisor`) |
| Producers | Supervisor panel, MegaDesk canvas (FE drop with BE), manual `XADD` |

### Fields

| Field | Required | Notes |
|-------|----------|-------|
| `node_endpoint` | yes | `BeSpec.name` from `MegaDesk.nodes` discovery |
| `parameters` | yes | JSON object of graph kvps (`FeSpec.backend_parameters`), or `""` |

### On consume

1. Assign `unique_id` (UUID4)
2. Resolve `node_endpoint` via `discover_backends()` / `get_backend`
3. Open `Logs/{session}/{node_endpoint}.md` (append); `Popen` with stdout/stderr redirected and `MEGADESK_*` env
4. `HSET RUNNINGNODES:<unique_id>` (DB 1) with identity, PID, `status=running`, `log_path`, `launched_at`

### Example

```text
XADD SUPERVISOR:LAUNCHREQUEST * node_endpoint machine_factory parameters ""
```

---

## SUPERVISOR:KILLREQUEST

| Property | Value |
|----------|-------|
| Type | Stream |
| Database | **0** |
| Key | `SUPERVISOR:KILLREQUEST` |
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
XADD SUPERVISOR:KILLREQUEST * node_endpoint machine_factory unique_id 3f2a9c1e-…
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
| `parameters` | Launch parameters (JSON object, or `""`) |
| `PID` | OS process id as a string |
| `status` | `running` or `exited` |
| `log_path` | Absolute path to the instance log file |
| `launched_at` | ISO-8601 UTC timestamp |
| `exit_code` | Process exit code as string; empty while `running` |
| `exited_at` | ISO-8601 UTC timestamp; empty while `running` |

Natural process death publishes `NODEEXIT` and **deletes** the hash. Dead
procs are not tracked. A Supervisor restart also sweeps Redis for hashes with
no `NODEHB` and outside the launch grace window.

### Example

```text
HSET RUNNINGNODES:3f2a9c1e-… node_endpoint machine_factory unique_id 3f2a9c1e-…
  parameters "" PID 12345 status running
  log_path C:/…/Logs/2026-08-17T20-55-03Z/machine_factory.md
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
XADD NODEEXIT * unique_id 3f2a9c1e-… node_endpoint machine_factory
  exit_code 1 log_path C:/…/Logs/2026-08-17T20-55-03Z/machine_factory.md
  exited_at 2026-08-06T20:01:00+00:00
```

---

## SUPERVISOR:SINGLETON

| Property | Value |
|----------|-------|
| Type | String |
| Database | **1** |
| Key | `SUPERVISOR:SINGLETON` (`SUPERVISOR_SINGLETON_KEY`) |
| Value | Owner token (typically Supervisor BE PID as string) |

Acquired when the Supervisor BE starts; a second BE exits if the lock is already held.
Released on clean shutdown.

---

## SUPERVISOR:ALIVE

| Property | Value |
|----------|-------|
| Type | String |
| Database | **1** |
| Key | `SUPERVISOR:ALIVE` (`SUPERVISOR_ALIVE_KEY`) |
| Value | `"1"` |
| TTL | 5 seconds (refreshed by Supervisor BE heartbeat) |

Presence of the key means the Supervisor BE process is reachable. Cleared on
shutdown. `ensure_supervisor_running()` / `SupervisorClient.backend_ok()` observe
this key on DB 1.

---

## Connection / provision

| Setting | Convention |
|---------|------------|
| Connection | **`REDIS_URL`** (default `redis://localhost:6379/0`) |
| Prefer | Attach to existing Redis at that URL |
| Else (loopback host only) | Docker container `megadesk-redis` (`redis:7`, host port from URL) + optional `megadesk-redis-insight` on port `5540` |

See `MegaDesk-Canvas/supervisor/redis_provision.py`. `SupervisorClient` and
`ensure_supervisor_running()` also honor `REDIS_URL`.

