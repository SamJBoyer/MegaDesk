# MegaDesk node protocol

Canonical reference for how Nodes are discovered, launched, hosted on the canvas, and torn down. Glossary: [`Docs/glossary.md`](glossary.md). Persistence shape of the board: [`MegaDesk-Canvas/docs/root.md`](../MegaDesk-Canvas/docs/root.md). Redis Supervisor streams: [`MegaDesk-contracts/redis/supervisor.md`](../MegaDesk-contracts/redis/supervisor.md).

A **node** is a modular tool inside MegaDesk. Nodes can expose a front-end (FE), a back-end (BE), or both. Discovery and launch are driven by packaging entry points.

| Half | What it is | Who uses it |
|------|------------|-------------|
| **FE** | Always Dear PyGui. Integrated into the MegaDesk canvas shell. | MegaDesk canvas (`MegaDesk-Canvas/`) via `get_fe_spec()` → `FeSpec` |
| **BE** | Long-lived process (argv + optional cwd). Managed as a subprocess. | Canvas-owned Supervisor BE via `get_be_spec()` → `BeSpec` |

Shared contract lives in the installable `megadesk-contracts` package (`MegaDesk-contracts/`): `FeSpec`, `BeSpec`, entry-point discovery, `SupervisorClient`, `frame_pump`, and logging helpers.

**Supervisor** is Canvas infrastructure (`MegaDesk-Canvas/supervisor/`), not a Catalog / `MegaDesk.nodes` entry. The BE starts on canvas launch via `megadesk_contracts.ensure_supervisor_running()` (`python -m supervisor`). The operator UI is a collapsible panel (`supervisor.panel.build_supervisor_panel`), not a droppable FE.

**Naming (MegaDesk vs legacy Executive):** MegaDesk uses `MegaDesk.nodes` + `FeSpec`/`BeSpec` + canvas host class `MegaDeskMember`. That is not the older Executive stack (`executive.nodes` / `BaseNode`).

---

## Project layout convention

Each productivity node is its own installable Python project under `Nodes/<Name>/`:

```text
Nodes/<Name>/
  pyproject.toml          # REQUIRED — registers MegaDesk.nodes
  <name>_node.py          # REQUIRED — get_fe_spec() / get_be_spec()
  …                       # app / package code for FE and/or BE
  Etc/Artwork/…           # optional icon for the Catalog palette
```

**Rules:**

1. Nodes **must** ship a `pyproject.toml` with a `[project.entry-points."MegaDesk.nodes"]` entry.
2. Nodes are always installed into the **MegaDesk conda env** (`pip install -e Nodes/<Name>`). Undiscoverable packages are invisible to FE and BE.
3. Depend on `megadesk-contracts` so the node can import `FeSpec` / `BeSpec` / `Mode`.
4. FE-only, BE-only, and FE+BE are all valid. Return `None` for the mode you do not support.
5. `get_fe_spec` / `get_be_spec` must return a ready-to-use launch description — the caller should not need extra setup beyond what the installed package already provides. `get_exec_spec(mode)` remains as a thin wrapper.

---

## Entry point registration

```toml
[project.entry-points."MegaDesk.nodes"]
my_tool = "my_tool_node:get_exec_spec"
```

- The **entry-point name** (`my_tool`) is the package’s discovery key.
- Prefer making `FeSpec.name` / `BeSpec.name` match that key so FE drop and BE `LAUNCHREQUEST` use the same nickname.
- One entry point serves both modes. Discovery prefers `get_fe_spec()` / `get_be_spec()` on the module; `get_exec_spec(mode)` is the compatibility wrapper.

Examples in-repo:

| Node | Entry point | Modes |
|------|-------------|-------|
| MissionControl | `mission_control` | FE + BE |
| MergeManager | `merge_manager` | FE only |
| TicketDispatcher | `ticket_dispatcher` | FE only |
| CodeScope | `code_scope` | FE + BE |
| VoiceDeck | `voice_deck` | FE + BE |
| CloudDispatcher | `cloud_dispatcher` | FE + BE |

---

## Required methods: `get_fe_spec()` / `get_be_spec()`

Every node module pointed at by the entry point exports:

```python
def get_fe_spec() -> FeSpec | None:
    ...

def get_be_spec() -> BeSpec | None:
    ...

def get_exec_spec(mode: Mode) -> FeSpec | BeSpec | None:
    if mode == "FE":
        return get_fe_spec()
    if mode == "BE":
        return get_be_spec()
    return None
```

`Mode` is `"FE"` | `"BE"`. Discovery calls `get_fe_spec` / `get_be_spec` when they exist.

### `FeSpec` (mode `"FE"`)

Front-end description for MegaDesk canvas hosting:

| Field | Type | Role |
|-------|------|------|
| `name` | `str` | Node nickname (palette / canvas / Redis launch) |
| `description` | `str` | Short blurb for Catalog |
| `icon` | `str \| None` | Path to an icon image, or `None` (Catalog uses a black square if empty/invalid) |
| `default_width` | `int` | Initial window width |
| `default_height` | `int` | Initial window height |
| `build` | callable | Builds the Dear PyGui UI into the host content parent |
| `backends` | `tuple[str, ...]` | Supervisor `node_endpoint` names to `XADD` on `LAUNCHREQUEST` when this FE is hosted (drop **or** canvas open). Empty = no BE. |

`build` signature:

```python
def build_ui(parent: str, *, tag_prefix: str, width: int, height: int) -> None:
    ...
```

**Contract:** fill the host content parent only — no `dpg.window`, no standalone viewport. MegaDesk owns the host `dpg.node` (label, close, position). Store a cleanup callable on the content parent with `dpg.set_item_user_data(parent, close_fn)` so the host can shut the FE down when deleting the member (the host may wrap that callable).

FEs that need a per-frame drain should use `megadesk_contracts.frame_pump.register` / `unregister`. The pump is a single module-global shared by every node on the board, so its state outlives any one FE — and outlives the DPG context. Whoever owns the context calls `frame_pump.reset()` on teardown; the canvas does this in `main()`. Integration tests drive the same pump: see [`Docs/integration_testing.md`](integration_testing.md).

FE-only example pattern:

```python
from megadesk_contracts import FeSpec, Mode

def get_fe_spec():
    return FeSpec(
        name="merge_manager",
        description="…",
        icon=icon_path_or_none,
        default_width=960,
        default_height=600,
        build=build_ui,
    )

def get_be_spec():
    return None
```

### `BeSpec` (mode `"BE"`)

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
  stdout=log_file,          # MegaDesk-Canvas/logs/<name>/<unique_id>.log
  stderr=STDOUT,
  env={…, MEGADESK_UNIQUE_ID, MEGADESK_NODE, MEGADESK_LOG_PATH},
)
```

BE processes should write diagnostics to stdout/stderr (or call
`megadesk_contracts.configure_node_logging()` at startup) and wrap `main` in
`NodeRuntime.from_env(...)` so they heartbeat their real `os.getpid()` every 5s
and exit when `NODE:SHUTDOWN` is `1` or Redis is unreachable. Supervisor merges
both streams into the per-instance log file under the **running** canvas
(`MEGADESK_CANVAS_ROOT` / cwd), not the worktree that last installed contracts.

BE-only example pattern:

```python
from megadesk_contracts import BeSpec, Mode

def get_be_spec():
    return BeSpec(
        name="mission_control",
        argv=[sys.executable, "-u", "-m", "MissionControlManager"],
        cwd=str(package_root),
    )

def get_fe_spec():
    return None
```

FE+BE example: `get_fe_spec()` returns an `FeSpec` with `backends=(name,)`; `get_be_spec()` returns the `BeSpec` (see `Nodes/MissionControl/mission_control_node.py`).

---

## Discovery API (`megadesk_contracts`)

Installed entry points are scanned via `importlib.metadata` group `MegaDesk.nodes`.

| Function | Returns |
|----------|---------|
| `discover_frontends()` | `dict[str, FeSpec]` — every node that returns an `FeSpec` for `"FE"` |
| `discover_backends()` | `dict[str, BeSpec]` — every node that returns a `BeSpec` for `"BE"` |
| `load_exec_spec(name, mode)` | One entry point’s result for that mode (matches **entry-point name**) |
| `get_backend(name)` | `BeSpec \| None` by nickname (also resolves via discovery keys / `BeSpec.name`) |
| `has_backend(name)` | Whether a BE exists for that name |

Keys prefer `spec.name`, falling back to the entry-point name.

Related public helpers (same package): `SupervisorClient`, `ensure_supervisor_running`, `configure_node_logging`, `frame_pump`, `resolve_redis_url`. Redis connection / DB constants: `DEFAULT_REDIS_URL`, `REDIS_DB_EPHEMERAL`, `REDIS_DB_PERSISTENT`, `SUPERVISOR_SINGLETON_KEY`, `SUPERVISOR_ALIVE_KEY`. All Redis clients honor **`REDIS_URL`**.

---

## How the FE uses nodes (MegaDesk canvas)

1. On startup, the canvas calls `ensure_supervisor_running()` so the Supervisor BE (`python -m supervisor`) is up before UI drop can request BE launches. Then it calls `discover_frontends()` (via `engine.megadesk_registry.discover_megadesk_frontends`) and fills the Catalog palette (`megadesk:<name>`). Icons come from `FeSpec.icon`. The Supervisor operator UI is built as collapsible chrome via `build_supervisor_panel` — it is not a Catalog entry.
2. Dropping a node places a canvas member (`type: "megadesk"`, `node_name` in `canvas.json`) and hosts the FE as a native `dpg.node` inside the canvas `node_editor` via `FeSpec.build`.
3. Nodes on the board are always the live FE (no placard / closed state). Middle-mouse pans the editor; there is no canvas zoom. Delete removes the selected node(s).
4. After drop **and** when a saved canvas is opened, the canvas reads `FeSpec.backends` and `XADD`s one `LAUNCHREQUEST` per endpoint (skipped if that BE is already alive, Redis is down, or Supervisor is not up).

Canvas-side install (also summarized in `MegaDesk-Canvas/readme.md`):

```bash
conda activate MEGADESK
pip install -e MegaDesk-contracts
pip install -e MegaDesk-Canvas
pip install -e Nodes/TicketDispatcher   # FE example
pip install -e Nodes/MergeManager       # FE example
pip install -e Nodes/MissionControl[canvas]     # FE + BE example
python main.py   # from MegaDesk-Canvas/ — starts Supervisor BE on launch
```

To reinstall every node from scratch, run `python scripts/refresh_nodes.py` from the MEGADESK env — it uninstalls and editable-reinstalls each `Nodes/<Name>/`, then verifies discovery. Before changing the Supervisor or a BE, run `python scripts/down_nodes.py`.

### Hosted shell (`MegaDeskMember`)

FEs are hosted inside native Dear PyGui `dpg.node` items in a `dpg.node_editor` (not floating `child_window` shells over a drawlist). The FE fills a host content parent via `FeSpec.build`.

| Owner | Responsibility |
| --- | --- |
| **Canvas** | `dpg.node` + static `node_attribute`, content parent, editor-grid position, delete |
| **FE `build()`** | Widgets inside the host content parent only — no `dpg.window`, no standalone viewport |

Closing via the node **x** (or Delete) removes the member from the board and from `canvas.json`.

### Canvas member persistence

Members are serialized into `canvas.json` (typically the repo-root file). Persistence is **members-only** (`{"members": {...}}`); legacy `hierarchy` / layers keys are ignored if present. Discriminator in JSON is **`type: "megadesk"`** (in-memory the host also keeps `global_guid = "megadesk"`).

| Field | Role |
| --- | --- |
| `canvas_id` | Instance GUID |
| `type` | Always `"megadesk"` |
| `nickname` | Display name (from `FeSpec.name`) |
| `node_name` | Discovery / FeSpec name |
| `position` | Editor-grid `(x, y)` |
| `scale` | Kept as `[1, 1]` for compatibility |
| `parents` / `children` | Serialized empty lists (legacy member-graph shape; unused) |
| `data` | FE payload: `width`, `height`, `node_name`, … |

`icon` and Catalog black-square fallback live on `FeSpec`, not on the member record.

### Engine interaction (brief)

`DisplayEngine` hosts members as `dpg.node` items, handles Catalog drop (editor-grid coords via a hidden reference node), BE launch on drop, Delete-key removal, and periodic position sync into the model. Node authors should implement behavior inside `FeSpec.build`, not by subclassing host lifecycle hooks.

---

## How the BE uses nodes (Supervisor)

1. The Supervisor BE refreshes backends with `discover_backends()`.
2. On `LAUNCHREQUEST` (Redis DB 0), it assigns a `unique_id`, resolves `get_backend(node_endpoint)`, redirects stdout/stderr to a log file under `MegaDesk-Canvas/logs/`, injects `MEGADESK_*` env, and writes `RUNNINGNODES:<unique_id>` on DB 1 (`status=running`, PID, `log_path`, …).
3. A reaper marks natural exits as `status=exited`, publishes `NODEEXIT` (DB 0), and keeps the hash until Stop. `KILLREQUEST` tears down (if needed) and `DEL`s the hash. See `MegaDesk-contracts/redis/supervisor.md`.

Callers outside the canvas can use `megadesk_contracts.SupervisorClient` (`launch_node`, `kill_node`, `list_running`, `get_running`, `kill_all_running`, `redis_ok`, `backend_ok`).

---

## Logging standard

| Half | Where diagnostics go |
|------|----------------------|
| **BE** | stdout/stderr → Supervisor file `MegaDesk-Canvas/logs/<endpoint>/<unique_id>.log`; prefer `megadesk_contracts.configure_node_logging()` |
| **FE** | Module logger (`logging.getLogger(…)`). Canvas host reports uncaught `FeSpec.build` / BE-launch failures instead of swallowing them silently. |

Do **not** put log line bodies on Redis streams.

Typical Redis path after an FE drop that also has a BE:

1. Caller `XADD`s `LAUNCHREQUEST` with `node_endpoint` and `parameters=""` (DB 0)
2. Supervisor BE consumes the stream and registers `RUNNINGNODES:<unique_id>` (DB 1)
3. There is no ack channel — observe `RUNNINGNODES:*` if confirmation is needed

---

## Minimal checklist for a new node

1. Create `Nodes/<Name>/` with `pyproject.toml` depending on `megadesk-contracts`.
2. Add `<name>_node.py` with `get_fe_spec()` / `get_be_spec()` returning `FeSpec` and/or `BeSpec`.
3. Register `[project.entry-points."MegaDesk.nodes"]`.
4. `pip install -e Nodes/<Name>` (add `[canvas]` / Dear PyGui if the node has an FE), or run `scripts/refresh_nodes.sh`, which picks the new node up automatically.
5. Restart MegaDesk so entry points are re-scanned (Supervisor BE starts with the canvas).
6. FE appears in Catalog; BE is launchable by endpoint once the Supervisor BE is alive.
