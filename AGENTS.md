<entry-point>

This project is named "MegaDesk" 

MegaDesk uses a Dear PyGui canvas that manages modular front-end and back-end nodes to create 
custom agentic workflows. 

Read /Docs and Docs/glossary for information about specific terms. For how Nodes are discovered, hosted, and launched (FE/BE), read Docs/node_protocol.md — it is the single authority. Canvas-package layout and DPG chrome: MegaDesk-Canvas/docs/canvas.md.

Always use the **MEGADESK** conda environment (`conda activate MEGADESK`). Do not run MegaDesk, pytest, or pip against any other interpreter.

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

Overview of code implementation: 
- MegaDesk-Canvas: MegaDesk-Canvas is the folder that contains the code for running the canvas that integrates the Nodes. 
- Nodes/: contains each node and its implementation. Read each node's README.md before editing. Some nodes are both Front-end and back-end. Always make sure you are placing the content in the appropriate place.
- MegaDesk-contracts: contains shared contracts between modules to ensure standardization. If a contract is modified or added make sure it is reflected in MegaDesk-contracts
- tests: contains unit tests for all implementations 
- scripts: helper scripts 

</entry-point>
<testing>

Most breakage here is at the seam between two modules, not inside one, so verify changes by running the integration suite rather than by reasoning about it: `pytest` from the repo root (needs a desktop session and a local Redis; ~30s). It boots the real canvas, presses real buttons and asserts the real Redis payloads. If you change a stream field, a widget tag, a callback wiring, or anything in `frame_pump`, that suite is the check. Read Docs/integration_testing.md before adding to it.

</testing>
<logging>

**Logs/** is the worktree-root session transcript folder. Read `Logs/CURRENT` (JSON pointer) then that timestamp folder. One `{node}.md` per node per Supervisor generation. Canvas open does not rotate or move these files — see `Docs/node_protocol.md` (Logging standard). 

</logging>
<Redis-policy>

We use different REDIS database for different levels of persistence. DB 0 is temporary and DB 1 is persistent. All clients connect via **`REDIS_URL`** (default `redis://localhost:6379/0`; see `DEFAULT_REDIS_URL` / `resolve_redis_url()` in `megadesk_contracts`). Do not hardcode host/port.




**DB 0 (ephemeral)** — streams / MissionControl default traffic:
- **LAUNCHREQUEST** — consume `node_endpoint` + `parameters` (JSON object of graph kvps, or `""`); discover BE via `MegaDesk.nodes` → `BeSpec`; `Popen` with `MEGADESK_*` env (including `MEGADESK_PARAMETERS`)
- **KILLREQUEST** — match `node_endpoint` + `unique_id`, graceful→force shutdown, `DEL` the RUNNINGNODES hash
- **NODEEXIT** — published on natural exit (metadata only; no log bodies)
- MissionControl `WORKORDER` / `AGENTHANDLER` / `FINISHED` also live here (same `REDIS_URL`, db 0)
- Voice chain: `CODEQ:ASK` / `CODEQ:ANSWER`, `VOICE:CONTROL` / `VOICE:EVENT`, `CLOUDORDER` / `CLOUDFINISHED` — defined once in `megadesk_contracts.wire`, documented in `MegaDesk-contracts/redis/voice-chain.md`. New streams go there, never in a per-node `redis_packets.py`. Audio never goes on a stream.

**DB 1 (persistent):**
- **GBD:SUPERVISOR:SINGLETON** — one-BE lock
- **GBD:SUPERVISOR:ALIVE** — heartbeat (TTL ~5s)
- **RUNNINGNODES:<unique_id>** — hash registry (`status`, PID, `log_path`, …) for **alive** nodes only
- **NODEHB:<unique_id>** — node heartbeat (`pid`, `status`, TTL ~15s)
- **NODE:SHUTDOWN** / **NODE:SHUTDOWN:<unique_id>** — kill switch (`1` stops the BE; Redis down also stops it)
- **CODESCOPE:SESSION:<id>** / **CLOUDRUN:<agent_id>** / **CLOUDDRAFT:<order_id>** — voice-chain state that must outlive its stream. Tests own those three prefixes plus `NODEHB:test-` / `NODE:SHUTDOWN:test-` on db 1 and never flush it.

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



