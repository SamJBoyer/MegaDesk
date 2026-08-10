
Context: We are trying to streamline the control flow. Having the supervisor as a seperate node creates an architectureal problem where too many responsibilities are divided between the Canvas and the supervisor node. We are going to combine the supervisor into the Canvas. We're also going to start using seperate databases in REDIS for different levels of persistence. We're also going to introduce a singleton pattern for the supervisor. 

Command: We're going to take Supervisor Node and integrate it into the canvas. The front-end is going to become a collapseable panel on the canvas and the back end is going to be launched by the canvas on startup. We're going to add a singleton flag for redis to ensure only 1 supervisor can ever be launched. We are going to use a new redis database scheme where the 0th database is default and the 1st is for persistent data. For example, the singleton flag and running procs should go to db1, and launchrequest, workorders, etc go to 0. Use a sub-agent to propogate this new change across the nodes and update the contracts. 

Testsables:
- there should be no more Nodes/Supervisor


## Supervisor BE (today)

Windows-side process lifecycle manager for node backends. Canvas infrastructure under `MegaDesk-Canvas/supervisor/` — **not** a Catalog / `MegaDesk.nodes` entry. Orchestrates work that is not attributable to any individual node: Redis provision, launch/kill, process registry, logging, and exit reaping.

- **BE:** canvas startup calls `megadesk_contracts.ensure_supervisor_running()` → `python -m supervisor` (not via `LAUNCHREQUEST`). Singleton on Redis DB 1 so only one BE can run.
- **FE:** collapsible panel via `supervisor.panel.build_supervisor_panel` (chrome, not a droppable node).
- **Logs:** `MegaDesk-Canvas/logs/<endpoint>/<unique_id>.log`; self-log `MegaDesk-Canvas/logs/supervisor/supervisor.log`.

### Redis provision
- Prefer an existing Redis on `localhost:6379`
- Else start Docker `gbd-redis` (`redis:7`) and optional `gbd-redis-insight` on port `5540`

### Control plane
**DB 0 (ephemeral)** — streams / Plant default traffic:
- **LAUNCHREQUEST** — consume `node_endpoint` (+ `parameters`, currently always `""`); discover BE via `MegaDesk.nodes` → `BeSpec`; `Popen` with `MEGADESK_*` env
- **KILLREQUEST** — match `node_endpoint` + `unique_id`, graceful→force shutdown, `DEL` the RUNNINGNODES hash
- **NODEEXIT** — published on natural exit (metadata only; no log bodies)
- Plant `WORKORDER` / `LIVEHARNESS` / `FINISHED` also live here (`redis://localhost:6379/0`)

**DB 1 (persistent):**
- **GBD:SUPERVISOR:SINGLETON** — one-BE lock
- **GBD:SUPERVISOR:ALIVE** — heartbeat (TTL ~5s)
- **RUNNINGNODES:<unique_id>** — hash registry (`status`, PID, `log_path`, …)

