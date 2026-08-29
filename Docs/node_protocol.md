# MegaDesk node protocol

Canonical reference for how Nodes are discovered, launched, hosted on the canvas, and torn down. Glossary: [`Docs/glossary.md`](glossary.md). Redis Supervisor streams: [`MegaDesk-Contracts/redis/supervisor.md`](../MegaDesk-Contracts/redis/supervisor.md). Canvas-package layout and DPG chrome: [`MegaDesk-Canvas/docs/canvas.md`](../MegaDesk-Canvas/docs/canvas.md).

A **node** is a modular tool inside MegaDesk. Nodes can expose a front-end (FE), a back-end (BE), or both. Discovery and launch are driven by packaging entry points.

| Half | What it is | Who uses it |
|------|------------|-------------|
| **FE** | Always Dear PyGui. Integrated into the MegaDesk canvas shell. | MegaDesk canvas (`MegaDesk-Canvas/`) via `get_fe_spec()` → `FeSpec` |
| **BE** | Long-lived process (argv + optional cwd). Managed as a subprocess. | Canvas-owned Supervisor BE via `get_be_spec()` → `BeSpec` |

Shared contract lives in the installable `megadesk-contracts` package (`MegaDesk-Contracts/`): `FeSpec`, `BeSpec`, entry-point discovery, `SupervisorClient`, `frame_pump`, and logging helpers.

**Supervisor** is Canvas infrastructure (`MegaDesk-Canvas/supervisor/`), not a Catalog / `MegaDesk.nodes` entry. The BE starts on canvas launch via `megadesk_contracts.ensure_supervisor_running()` (`python -m supervisor`). The operator UI is a right-hand collapsible pane with Nodes and Logs tabs (`supervisor.panel.build_supervisor_panel`), matching the left Catalog — not a droppable FE.

**VoiceDeck** is the same shape: the FE is canvas chrome (`voice_deck.panel.build_voice_deck_panel`), a collapsible strip that always boots on launch — not a Catalog entry. The BE keeps its `voice_deck` identity (`get_be_spec()` / `VoiceDeckManager`) and is started once via `ensure_voice_deck_running()` after Supervisor is up, skipped if that endpoint is already in RUNNINGNODES.

---

## Project layout convention

Each productivity node is its own installable Python project under `Nodes/<Name>/`:

```text
Nodes/<Name>/
  pyproject.toml          # REQUIRED — registers MegaDesk.nodes
  <name>_node.py          # REQUIRED — get_fe_spec() / get_be_spec()
  parameters.yaml         # optional — declared graph parameter names
  …                       # app / package code for FE and/or BE
  Etc/Artwork/…           # optional icon for the Catalog palette
```

**Rules:**

1. Nodes **must** ship a `pyproject.toml` with a `[project.entry-points."MegaDesk.nodes"]` entry.
2. Nodes are always installed into the **MegaDesk conda env** (`pip install -e Nodes/<Name>`). Undiscoverable packages are invisible to FE and BE.
3. Depend on `megadesk-contracts` so the node can import `FeSpec` / `BeSpec`.
4. FE-only, BE-only, and FE+BE are all valid. Return `None` for the mode you do not support.
5. `get_fe_spec` / `get_be_spec` must return a ready-to-use launch description — the caller should not need extra setup beyond what the installed package already provides.

---

## Entry point registration

```toml
[project.entry-points."MegaDesk.nodes"]
my_tool = "my_tool_node"
```

- The **entry-point name** (`my_tool`) is the package’s discovery key.
- Prefer making `FeSpec.name` / `BeSpec.name` match that key so FE drop and BE `LAUNCHREQUEST` use the same nickname.
- The value is the node module. Discovery loads it and calls `get_fe_spec()` / `get_be_spec()`. One entry point serves both halves.

Examples in-repo:

| Node | Entry point | Modes |
|------|-------------|-------|
| MachineFactory (`Nodes/Factory/MachineFactory`) | `machine_factory` | FE + BE |
| CloudFactory (`Nodes/Factory/CloudFactory`) | `cloud_factory` | FE + BE |
| PRManager | `pr_manager` | FE only |
| WorkDispatcher (`Nodes/HumanGates/WorkDispatcher`) | `work_dispatcher` | FE only |
| AutoIntegrate (`Nodes/HumanGates/AutoIntegrate`) | `auto_integrate` | FE only |
| CodeScope | `code_scope` | FE + BE |
| VoiceDeck | `voice_deck` | BE only (FE is canvas chrome) |
| GraphScope | `graph_scope` | FE only |
| VisionBoard | `vision_board` | FE only |

Nodes may be nested. Related ones are grouped by folder — `Nodes/Factory/` holds
the two factories as siblings, `Nodes/HumanGates/` the two gates — and
`scripts/refresh_nodes.py` discovers at any depth. Nesting groups nodes; it does not merge them: each keeps its own
`pyproject.toml`, its own entry point and its own identity on the canvas.

---

## Required methods: `get_fe_spec()` / `get_be_spec()`

Every node module pointed at by the entry point exports:

```python
def get_fe_spec(parameters: Mapping[str, str] | None = None) -> FeSpec | None:
    ...

def get_be_spec() -> BeSpec | None:
    ...
```

Nodes that do not take parameters may keep a zero-argument `get_fe_spec()`; the caller only passes `parameters=` when the function accepts it.

`parameters` are the string kvps a **graph** saved for this member (see *Graph parameters* below). `get_fe_spec` folds them into the returned spec — `build` closes over them, `backend_parameters` is the subset (or rewrite) to drop onto `SUPERVISOR:LAUNCHREQUEST`. The host still calls `build(parent, *, tag_prefix, width, height)` with no parameters kwarg.

### `FeSpec`

Front-end description for MegaDesk graph hosting:

| Field | Type | Role |
|-------|------|------|
| `name` | `str` | Node nickname (palette / graph / Redis launch) |
| `description` | `str` | Short blurb for Catalog |
| `icon` | `str \| None` | Path to an icon image, or `None` (Catalog uses a black square if empty/invalid) |
| `default_width` | `int` | Initial window width |
| `default_height` | `int` | Initial window height |
| `build` | callable | Builds the Dear PyGui UI into the host content parent |
| `backends` | `tuple[str, ...]` | Supervisor `node_endpoint` names to `XADD` on `SUPERVISOR:LAUNCHREQUEST` when this FE is hosted (drop **or** graph open). Empty = no BE. |
| `parameters` | `tuple[str, ...]` | Names this node recognizes, usually from its `parameters.yaml`. Empty = none. |
| `backend_parameters` | `Mapping[str, str]` | Packet dropped onto `SUPERVISOR:LAUNCHREQUEST` `parameters` (subset or rewrite of the graph values). Empty = `""` on the wire. |
| `read_parameters` | `callable \| None` | `(tag_prefix) -> mapping` so the graph bar can Capture live sub-GUI values |

`build` signature:

```python
def build_ui(parent: str, *, tag_prefix: str, width: int, height: int) -> None:
    ...
```

**Contract:** fill the host content parent only — no `dpg.window`, no standalone viewport. MegaDesk owns the host `dpg.node` (label, close, position). Store a cleanup callable on the content parent with `dpg.set_item_user_data(parent, close_fn)` so the host can shut the FE down when deleting the member (the host may wrap that callable).

FEs that need a per-frame drain should use `megadesk_contracts.frame_pump.register` / `unregister`. The pump is a single module-global shared by every node on the board, so its state outlives any one FE — and outlives the DPG context. Whoever owns the context calls `frame_pump.reset()` on teardown; the canvas does this in `main()`. Integration tests drive the same pump: see [`Docs/integration_testing.md`](integration_testing.md).

FE-only example pattern:

```python
from megadesk_contracts import FeSpec

def get_fe_spec():
    return FeSpec(
        name="pr_manager",
        description="…",
        icon=icon_path_or_none,
        default_width=960,
        default_height=600,
        build=build_ui,
    )

def get_be_spec():
    return None
```

### `BeSpec`

Back-end launch instruction for the Canvas-owned Supervisor:

| Field | Type | Role |
|-------|------|------|
| `name` | `str` | Managed nickname (`LAUNCHREQUEST` `node_endpoint`) |
| `argv` | `list[str]` | Full process argv |
| `cwd` | `str \| None` | Working directory (defaults to `None`; often the node package root) |

Launch contract (Supervisor owns capture):

```text
subprocess.Popen(
  BeSpec.argv,
  cwd=BeSpec.cwd,
  stdout=log_file,          # Logs/{session}/{name}.md
  stderr=STDOUT,
  env={…, MEGADESK_UNIQUE_ID, MEGADESK_NODE, MEGADESK_LOG_PATH, MEGADESK_PARAMETERS},
)
```

BE processes should write diagnostics to stdout/stderr (or call
`megadesk_contracts.configure_node_logging()` at startup) and wrap `main` in
`NodeRuntime.from_env(...)` so they heartbeat their real `os.getpid()` every 5s
and exit when `NODE:SHUTDOWN` is `1` or Redis is unreachable. Supervisor merges
both streams into one `{node}.md` per node under the worktree `Logs/{session}/`
(see *Logging standard* below), not the worktree that last installed contracts.

BE-only example pattern:

```python
from megadesk_contracts import BeSpec

def get_be_spec():
    return BeSpec(
        name="machine_factory",
        argv=[sys.executable, "-u", "-m", "MachineFactoryManager"],
        cwd=str(package_root),
    )

def get_fe_spec():
    return None
```

FE+BE example: `get_fe_spec()` returns an `FeSpec` with `backends=(name,)`; `get_be_spec()` returns the `BeSpec` (see `Nodes/Factory/MachineFactory/machine_factory_node.py`).

---

## Discovery API (`megadesk_contracts`)

Installed entry points are scanned via `importlib.metadata` group `MegaDesk.nodes`.

| Function | Returns |
|----------|---------|
| `discover_frontends()` | `dict[str, FeSpec]` — every node that returns an `FeSpec` |
| `discover_backends()` | `dict[str, BeSpec]` — every node that returns a `BeSpec` |
| `load_fe_spec(name, parameters=None)` | One FE spec, rebuilt with graph parameters when given |
| `load_be_spec(name)` | One BE spec (matches entry-point name or `BeSpec.name`) |
| `get_backend(name)` | `BeSpec \| None` by nickname (also resolves via discovery keys / `BeSpec.name`) |

Keys prefer `spec.name`, falling back to the entry-point name.

Related public helpers (same package): `SupervisorClient`, `ensure_supervisor_running`, `configure_node_logging`, `frame_pump`, `resolve_redis_url`, `resolve_redis_pair`. Redis connection / DB constants: `DEFAULT_REDIS_URL`, `REDIS_DB_EPHEMERAL`, `REDIS_DB_PERSISTENT`, `SUPERVISOR_SINGLETON_KEY`, `SUPERVISOR_ALIVE_KEY`. All Redis clients honor **`REDIS_URL`** and select ephemeral/persistent via `resolve_redis_pair()`.

---

## How the FE uses nodes (MegaDesk graph)

1. On startup, MegaDesk-Canvas calls `ensure_supervisor_running()` so the Supervisor BE (`python -m supervisor`) is up before UI drop can request BE launches, then `ensure_voice_deck_running()` so the VoiceDeck BE (`voice_deck`) is launched once (skipped if already in RUNNINGNODES). Then it calls `discover_frontends()` (via `engine.megadesk_registry.discover_megadesk_frontends`) and fills the Catalog palette (`megadesk:<name>`). Icons come from `FeSpec.icon`. A **graph bar** sits above the Catalog so the operator can pick, save, save-as, Capture, or delete a graph. The Supervisor operator UI is a right-hand collapsible pane (Nodes / Logs tabs) via `build_supervisor_panel` — it is not a Catalog entry. Selecting a hosted node on the board shows that node's session log in the Logs tab. The VoiceDeck operator UI is a collapsible strip under the canvas row via `build_voice_deck_panel` — also not a Catalog entry.
2. Dropping a node places a graph member (`type: "megadesk"`, `node_name` in the open `.json`) and hosts the FE as a native `dpg.node` inside the graph `node_editor` via `FeSpec.build`. Graph parameters for that member are passed into `get_fe_spec(parameters=…)`.
3. Nodes on the board are always the live FE (no placard / closed state). Middle-mouse pans the editor; there is no graph zoom. Delete removes the selected node(s).
4. After drop **and** when a saved graph is opened, the host reads `FeSpec.backends` and `XADD`s one `SUPERVISOR:LAUNCHREQUEST` per endpoint with `parameters` set to `FeSpec.backend_parameters` (skipped if that BE is already alive, Redis is down, or Supervisor is not up).

Any `.json` file can be opened as a graph. Files that are not a graph (`members` missing, invalid JSON, unknown member `type`) raise `GraphError`; the graph bar shows the message and leaves the open graph untouched.

Canvas-side install (also summarized in `MegaDesk-Canvas/readme.md`):

```bash
conda activate MEGADESK
pip install -e MegaDesk-Contracts
pip install -e MegaDesk-Canvas
pip install -e Nodes/HumanGates/WorkDispatcher   # FE example
pip install -e Nodes/PRManager          # FE example
pip install -e Nodes/Factory/MachineFactory[canvas]   # FE + BE example, nested
python main.py   # from MegaDesk-Canvas/ — starts Supervisor BE on launch
```

To reinstall every node from scratch, run `python scripts/refresh_nodes.py` from the MEGADESK env — it uninstalls and editable-reinstalls every node under `Nodes/`, at any depth, then verifies discovery. It skips anything inside a nested git checkout, because CodeScope clones repos into `Nodes/CodeScope/Scope/` and one of them is usually MegaDesk itself. Before changing the Supervisor or a BE, run `python scripts/down_nodes.py`. To refresh contracts + canvas, run `python scripts/refresh_contracts.py`; to rebuild the MachineFactory sandbox image, `python scripts/rebuild_sandbox.py`. `python scripts/master_refresh.py` runs down → contracts → nodes → sandbox.

### Hosted shell (`MegaDeskMember`)

FEs are hosted inside native Dear PyGui `dpg.node` items in a `dpg.node_editor` (not floating `child_window` shells over a drawlist). The FE fills a host content parent via `FeSpec.build`.

| Owner | Responsibility |
| --- | --- |
| **Canvas** | `dpg.node` + static `node_attribute`, content parent, editor-grid position, delete |
| **FE `build()`** | Widgets inside the host content parent only — no `dpg.window`, no standalone viewport |

Closing via the node **x** (or Delete) removes the member from the board and from the open graph file.

### Graph parameters

A node that takes parameters ships `parameters.yaml` next to its entry-point module: a flat list of recognized names (`#` starts a comment). Example (`Nodes/HumanGates/WorkDispatcher/parameters.yaml`):

```yaml
- GIT_URL # the http of the git repo this node will connect to
- ISSUE_LABEL # the issue label this gate tracks (default agent-ready)
```

Helpers live in `megadesk_contracts.parameters` (`load_parameter_names`, `normalize_parameters`, `parameters_to_json`, `parameters_from_env`). The graph stores a value per declared name per member. **Capture** on the graph bar reads live sub-GUI values via `FeSpec.read_parameters` and writes them into the graph. What a node does with incoming parameters is its own business — WorkDispatcher and PRManager seed the repo URL field from `GIT_URL`.

A Supervisor-launched BE reads the same packet from `MEGADESK_PARAMETERS` (JSON object, or empty).

### Graph member persistence

Members are serialized into a graph `.json` (default `Graphs/default.json`; the bar can point at any file). Boot prefers the last open path in `Graphs/CURRENT` when that file still points at a valid graph. Persistence is **members-only** (`{"members": {...}}`). Discriminator in JSON is **`type: "megadesk"`**.

| Field | Role |
| --- | --- |
| `member_id` | Instance GUID |
| `type` | Always `"megadesk"` |
| `nickname` | Display name (from `FeSpec.name`) |
| `node_name` | Discovery / FeSpec name |
| `position` | Editor-grid `(x, y)` |
| `parameters` | String kvps for names declared in the node's `parameters.yaml` |
| `data` | FE payload: `width`, `height`, `node_name`, … |

`icon` and Catalog black-square fallback live on `FeSpec`, not on the member record.

### Engine interaction (brief)

`DisplayEngine` hosts members as `dpg.node` items, handles Catalog drop (editor-grid coords via a hidden reference node), BE launch on drop, Delete-key removal, and periodic position sync into the model. Node authors should implement behavior inside `FeSpec.build`, not by subclassing host lifecycle hooks.

---

## How the BE uses nodes (Supervisor)

1. The Supervisor BE refreshes backends with `discover_backends()`.
2. On `SUPERVISOR:LAUNCHREQUEST` (Redis DB 0), it assigns a `unique_id`, resolves `get_backend(node_endpoint)`, redirects stdout/stderr to `Logs/{session}/{endpoint}.md`, injects `MEGADESK_*` env, and writes `RUNNINGNODES:<unique_id>` on DB 1 (`status=running`, PID, `log_path`, …).
3. A reaper marks natural exits as `status=exited`, publishes `NODEEXIT` (DB 0), and keeps the hash until Stop. `SUPERVISOR:KILLREQUEST` tears down (if needed) and `DEL`s the hash. See `MegaDesk-Contracts/redis/supervisor.md`.

Callers outside the canvas can use `megadesk_contracts.SupervisorClient` (`launch_node`, `kill_node`, `list_running`, `get_running`, `kill_all_running`, `redis_ok`, `backend_ok`).

---

## Logging standard

Session transcripts live at the **worktree** `Logs/` (sibling of `Docs/` and `Nodes/`), not under `MegaDesk-Canvas/`. A session is one **Supervisor generation**, not a canvas open: `ensure_supervisor_running()` creates a new timestamp folder only when it actually spawns a Supervisor. If Supervisor is already alive, canvas reopen appends to the same session. Files are born in their session folder and are never moved (BEs hold those files open).

```text
Logs/
  CURRENT                      # JSON pointer (session, started_at, supervisor_pid)
  README.md
  2026-08-17T20-55-03Z/
    supervisor.md
    canvas.md
    machine_factory.md
    agent-<guid>.md            # pretty MachineFactory sandbox transcript
    agent-<guid>.tokens.md     # token-by-token SDK stream for the same run
```

Read `Logs/CURRENT`, then that folder. Older timestamp folders are previous generations.

| Half | Where diagnostics go |
|------|----------------------|
| **BE** | stdout/stderr → Supervisor file `Logs/{session}/{endpoint}.md` (one file per node per session, append; launch/exit banners carry `unique_id`); prefer `megadesk_contracts.configure_node_logging()` |
| **FE / canvas** | Module logger. Canvas also appends `canvas.md` in the current session. Host reports uncaught `FeSpec.build` / BE-launch failures instead of swallowing them silently. |
| **Agent sandbox** | `Logs/{session}/agent-{guid}.md` — pretty transcript (coalesced thinking/assistant). `Logs/{session}/agent-{guid}.tokens.md` — token-by-token SDK stream. Both bind-mounted into the container as files. Not Redis payloads. |

Helpers: `begin_log_session` / `attach_log_session` / `session_log_path` in `megadesk_contracts`. Env: `MEGADESK_LOGS_ROOT` (`Logs/` home), `MEGADESK_LOGS_DIR` (live session folder).

Do **not** put log line bodies on Redis streams.

Typical Redis path after an FE drop that also has a BE:

1. Caller `XADD`s `SUPERVISOR:LAUNCHREQUEST` with `node_endpoint` and `parameters` (JSON object of `FeSpec.backend_parameters`, or `""` when empty) (DB 0)
2. Supervisor BE consumes the stream, injects `MEGADESK_PARAMETERS`, and registers `RUNNINGNODES:<unique_id>` (DB 1)
3. There is no ack channel — observe `RUNNINGNODES:*` if confirmation is needed

---

## Minimal checklist for a new node

1. Create `Nodes/<Name>/` with `pyproject.toml` depending on `megadesk-contracts`.
2. Add `<name>_node.py` with `get_fe_spec()` / `get_be_spec()` returning `FeSpec` and/or `BeSpec`.
3. Register `[project.entry-points."MegaDesk.nodes"]`.
4. `pip install -e Nodes/<Name>` (add `[canvas]` / Dear PyGui if the node has an FE), or run `python scripts/refresh_nodes.py`, which picks the new node up automatically.
5. Restart MegaDesk so entry points are re-scanned (Supervisor BE starts with the canvas).
6. FE appears in Catalog; BE is launchable by endpoint once the Supervisor BE is alive.
