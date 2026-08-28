<entry-point>

This project is named "MegaDesk" 

MegaDesk uses a Dear PyGui canvas that manages modular front-end and back-end nodes to create 
custom agentic workflows. 

Read /Docs and Docs/glossary for information about specific terms. For how Nodes are discovered, hosted, and launched (FE/BE), read Docs/node_protocol.md — it is the single authority. Canvas-package layout and DPG chrome: MegaDesk-Canvas/docs/canvas.md.

Always use the **MEGADESK** conda environment (`conda activate MEGADESK`). Do not run MegaDesk, pytest, or pip against any other interpreter.

Cursor Cloud agents: `.cursor/environment.json` installs Miniconda at `~/miniconda3`, creates `MEGADESK` (Python 3.13), and starts a native Redis on `localhost:6379`. Docker is not required for Redis.

Before working, always make sure the supervisor is down by running. 

```bash
conda activate MEGADESK
python scripts/down_nodes.py
```

This will stop live locks from interfering with your workflow. 

After changing any node package (`pyproject.toml`, entry points, or installed modules), reinstall from this worktree so the env does not keep a stale install:

```bash
conda activate MEGADESK
python scripts/refresh_nodes.py
```

After changing contracts or canvas packaging, or to rebuild the MachineFactory sandbox image:

```bash
conda activate MEGADESK
python scripts/refresh_contracts.py   # megadesk-contracts + megadesk-canvas
python scripts/rebuild_sandbox.py     # machine-factory-agent:latest
python scripts/master_refresh.py      # down → contracts → nodes → sandbox
```

Overview of code implementation: 
- MegaDesk-Canvas: MegaDesk-Canvas is the folder that contains the code for running the canvas that integrates the Nodes. 
- Nodes/: contains each node and its implementation. Read each node's README.md before editing. Some nodes are both Front-end and back-end. Always make sure you are placing the content in the appropriate place. Nodes may be grouped in a subfolder when they are variants of one idea — `Nodes/Factory/` holds MachineFactory and CloudFactory — but each stays its own package with its own entry point. Read `Nodes/Factory/README.md` before touching either factory.
- MegaDesk-Contracts: contains shared contracts between modules to ensure standardization. If a contract is modified or added make sure it is reflected in MegaDesk-Contracts
- tests: contains unit tests for all implementations 
- scripts: helper scripts 

</entry-point>
<testing>

Most breakage here is at the seam between two modules, not inside one, so verify changes by running the integration suite rather than by reasoning about it: `pytest` from the repo root (needs a desktop session and a local Redis; ~30s). It boots the real canvas, presses real buttons and asserts the real Redis payloads. If you change a stream field, a widget tag, a callback wiring, or anything in `frame_pump`, that suite is the check. Read Docs/integration_testing.md before adding to it.

</testing>
<logging>

**Logs/** is the worktree-root session transcript folder. Read `Logs/CURRENT` (JSON pointer) then that timestamp folder. One `{node}.md` per node per Supervisor generation, plus `agent-{guid}.md` / `agent-{guid}.tokens.md` per MachineFactory run. Canvas open does not rotate or move these files — see `Docs/node_protocol.md` (Logging standard). 

</logging>
<Redis-policy>

We use a Redis **pair** per MegaDesk process. DB 0 is the live ephemeral bus and DB 1 is live persistent state. All clients connect via **`REDIS_URL`** (default `redis://localhost:6379/0`; see `DEFAULT_REDIS_URL` / `resolve_redis_url()` / `resolve_redis_pair()` in `megadesk_contracts`). Do not hardcode host/port. `REDIS_URL` names the ephemeral index; persistent is that index + 1, except URLs that name db 0 or 1 stay on the live pair.

**DB 0 (live ephemeral)** — streams / default node traffic:
- **SUPERVISOR:LAUNCHREQUEST** — consume `node_endpoint` + `parameters` (JSON object of graph kvps, or `""`); discover BE via `MegaDesk.nodes` → `BeSpec`; `Popen` with `MEGADESK_*` env (including `MEGADESK_PARAMETERS`)
- **SUPERVISOR:KILLREQUEST** — match `node_endpoint` + `unique_id`, graceful→force shutdown, `DEL` the RUNNINGNODES hash
- **NODEEXIT** — published on natural exit (metadata only; no log bodies)
- MachineFactory `WORKORDER` / `AGENTHANDLER` / `FINISHED` also live here (same `REDIS_URL`, db 0)
- Voice chain: `CODEQ:ASK` / `CODEQ:ANSWER`, `VOICE:CONTROL` / `VOICE:EVENT`, `CLOUDORDER` / `CLOUDFINISHED` — defined once in `megadesk_contracts.wire`, documented in `MegaDesk-Contracts/redis/voice-chain.md`. Audio never goes on a stream.

Every stream and hash is defined exactly once, in `megadesk_contracts.wire`, and imported by every writer. A node must never ship its own `redis_packets.py`: `WORKORDER` used to be defined twice and the copies were free to drift. Both factories additionally share one status vocabulary (`wire/factory.py`) and one Python surface (`megadesk_contracts.factory.AgentFactory`: launch / poll / cancel), so a graph controller can place an agent locally or in the cloud without the two behaving differently.

**DB 1 (live persistent):**
- **SUPERVISOR:SINGLETON** — one-BE lock
- **SUPERVISOR:ALIVE** — heartbeat (TTL ~5s)
- **RUNNINGNODES:<unique_id>** — hash registry (`status`, PID, `log_path`, …) for **alive** nodes only
- **NODEHB:<unique_id>** — node heartbeat (`pid`, `status`, TTL ~15s)
- **NODE:SHUTDOWN** / **NODE:SHUTDOWN:<unique_id>** — kill switch (`1` stops the BE; Redis down also stops it)
- **CODESCOPE:SESSION:<id>** / **CLOUDRUN:<agent_id>** — voice-chain state that must outlive its stream

MachineFactory sandboxes get a Redis **sidecar** injected as agent `REDIS_URL` so MegaDesk inside the container never shares the host live pair; factory IPC stays on `MEGADESK_FACTORY_REDIS_URL` (the factory process's ephemeral DB on the host). Host pytest owns **14/15** and is never handed to an agent. Live 0/1 is never flushed except MegaDesk-Canvas boot when `DEV_FLUSH_MODE` is on (`1` / `true` / `yes` / `on`; default on — disable with `0` / `false` / `no` / `off`). That path calls `flush_live_redis_pair()` before `ensure_supervisor_running()` so the new supervisor recreates groups and hashes on empty DBs. Pytest, `python -m supervisor` alone, and agent sandboxes still refuse 0/1.

</Redis-policy>
<FE-Design>

When designing the GUIs pay attention to these constraints:
	
This GUIS are going to combined on a master canvas which will have many other GUIS.
Because of this, you must be conservative with the amount of space you use. 

<DO-NOT>
- add verbose text to the GUIS that explain the functions, this is a waste of space. 
- double-add titles as this is a waste of space. 
- add any new features not explicitly requested
- preallocate more than 2 spaces in scrollboxes and lists that will populate with elements. 
</DO-NOT>

<DO>
- Prefer sleek and simplistic GUIS
- Prefer compact style with thin margins 
- Allocate space to fill scrollboxes and lists with items AS they populate
</DO>

</FE-Design>



