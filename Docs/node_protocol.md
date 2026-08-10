# MegaDesk node protocol

Canonical reference for how Nodes are discovered, launched, hosted on the canvas, and torn down. Glossary: [`Docs/glossary.md`](glossary.md). Persistence shape of the board: [`MegaDesk-Canvas/docs/root.md`](../MegaDesk-Canvas/docs/root.md). Redis Supervisor streams: [`MegaDesk-contracts/redis/supervisor.md`](../MegaDesk-contracts/redis/supervisor.md).

A **node** is a modular tool inside MegaDesk. Nodes can expose a front-end (FE), a back-end (BE), or both. Discovery and launch are driven by packaging entry points.

| Half | What it is | Who uses it |
|------|------------|-------------|
| **FE** | Always Dear PyGui. Integrated into the MegaDesk canvas shell. | MegaDesk canvas (`MegaDesk-Canvas/`) via `get_exec_spec("FE")` → `FeSpec` |
| **BE** | Long-lived process (argv + optional cwd). Managed as a subprocess. | Canvas-owned Supervisor BE via `get_exec_spec("BE")` → `BeSpec` |

Shared contract lives in the installable `megadesk-contracts` package (`MegaDesk-contracts/`): `FeSpec`, `BeSpec`, entry-point discovery, `SupervisorClient`, `frame_pump`, and logging helpers.

**Supervisor** is Canvas infrastructure (`MegaDesk-Canvas/supervisor/`), not a Catalog / `MegaDesk.nodes` entry. The BE starts on canvas launch via `megadesk_contracts.ensure_supervisor_running()` (`python -m supervisor`). The operator UI is a collapsible panel (`supervisor.panel.build_supervisor_panel`), not a droppable FE.

**Naming (MegaDesk vs legacy Executive):** MegaDesk uses `MegaDesk.nodes` + `FeSpec`/`BeSpec` + canvas host class `MegaDeskMember`. That is not the older Executive stack (`executive.nodes` / `BaseNode`).

---

## Project layout convention

Each productivity node is its own installable Python project under `Nodes/<Name>/`:

```text
Nodes/<Name>/
  pyproject.toml          # REQUIRED — registers MegaDesk.nodes
  <name>_node.py          # REQUIRED — get_exec_spec(mode)
  …                       # app / package code for FE and/or BE
  Etc/Artwork/…           # optional icon for the Catalog palette
```

**Rules:**

1. Nodes **must** ship a `pyproject.toml` with a `[project.entry-points."MegaDesk.nodes"]` entry.
2. Nodes are always installed into the **MegaDesk conda env** (`pip install -e Nodes/<Name>`). Undiscoverable packages are invisible to FE and BE.
3. Depend on `megadesk-contracts` so the node can import `FeSpec` / `BeSpec` / `Mode`.
4. FE-only, BE-only, and FE+BE are all valid. Return `None` for the mode you do not support.
5. `get_exec_spec` must return a ready-to-use launch description — the caller should not need extra setup beyond what the installed package already provides.

---

## Entry point registration

```toml
[project.entry-points."MegaDesk.nodes"]
my_tool = "my_tool_node:get_exec_spec"
```

- The **entry-point name** (`my_tool`) is the package’s discovery key.
- Prefer making `FeSpec.name` / `BeSpec.name` match that key so FE drop and BE `LAUNCHREQUEST` use the same nickname.
- One entry point serves both modes; branching happens inside `get_exec_spec`.

Examples in-repo:

| Node | Entry point | Modes |
|------|-------------|-------|
| Plant | `plant` | FE + BE |
| MergeManager | `merge_manager` | FE only |
| TicketDispatcher | `ticket_dispatcher` | FE only |

---

## Required method: `get_exec_spec(mode)`

Every node module pointed at by the entry point exports:

```python
def get_exec_spec(mode: Mode) -> FeSpec | BeSpec | None:
    ...
```

`Mode` is `"FE"` | `"BE"`.

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

`build` signature:

```python
def build_ui(parent: str, *, tag_prefix: str, width: int, height: int) -> None:
    ...
```

**Contract:** fill the host content parent only — no `dpg.window`, no standalone viewport. MegaDesk owns the shell (header, close, position, size). Store a cleanup callable on the content parent with `dpg.set_item_user_data(parent, close_fn)` so the host can shut the FE down when collapsing to a placard or deleting the member (the host may wrap that callable).

FEs that need a per-frame drain should use `megadesk_contracts.frame_pump.register` / `unregister`.

FE-only example pattern:

```python
from megadesk_contracts import FeSpec, Mode

def get_exec_spec(mode: Mode):
    if mode == "FE":
        return FeSpec(
            name="merge_manager",
            description="…",
            icon=icon_path_or_none,
            default_width=960,
            default_height=600,
            build=build_ui,
        )
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
`megadesk_contracts.configure_node_logging()` at startup). Supervisor merges both streams
into the per-instance log file and records `log_path` / exit metadata on
`RUNNINGNODES:<unique_id>` (Redis DB 1).

BE-only example pattern:

```python
from megadesk_contracts import BeSpec, Mode

def get_exec_spec(mode: Mode):
    if mode == "BE":
        return BeSpec(
            name="plant",
            argv=[sys.executable, "-u", "-m", "PlantManager"],
            cwd=str(package_root),
        )
    return None
```

FE+BE example: return `FeSpec` for `"FE"` and `BeSpec` for `"BE"` from the same function (see `Nodes/Plant/plant_node.py`).

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

Related public helpers (same package): `SupervisorClient`, `ensure_supervisor_running`, `configure_node_logging`, `frame_pump`. Redis DB / key constants: `REDIS_DB_EPHEMERAL`, `REDIS_DB_PERSISTENT`, `SUPERVISOR_SINGLETON_KEY`, `SUPERVISOR_ALIVE_KEY`.

---

## How the FE uses nodes (MegaDesk canvas)

1. On startup, the canvas calls `ensure_supervisor_running()` so the Supervisor BE (`python -m supervisor`) is up before UI drop can request BE launches. Then it calls `discover_frontends()` (via `engine.megadesk_registry.discover_megadesk_frontends`) and fills the Catalog palette (`megadesk:<name>`). Icons come from `FeSpec.icon`. The Supervisor operator UI is built as collapsible chrome via `build_supervisor_panel` — it is not a Catalog entry.
2. Dropping a node places a canvas member (`type: "megadesk"`, `node_name` in `canvas.json`) and builds the FE into a host-owned shell under `canvas_window` via `FeSpec.build`.
3. Pan/zoom keeps the shell world-anchored; open shells size at `world * view_zoom`. Close leaves a placard; double-click reopens the GUI. Open/closed is restored from `canvas.json` (`data.gui_open`).
4. If `has_backend(node_name)` is true after drop: only if Redis is reachable **and** Supervisor is already alive, the canvas `XADD`s `LAUNCHREQUEST` with `node_endpoint` = node name (and `parameters=""`). Otherwise the BE launch is skipped.

Canvas-side install (also summarized in `MegaDesk-Canvas/readme.md`):

```bash
conda activate <MegaDesk-env>
pip install -e MegaDesk-contracts
pip install -e MegaDesk-Canvas
pip install -e Nodes/TicketDispatcher   # FE example
pip install -e Nodes/MergeManager       # FE example
pip install -e Nodes/Plant              # FE + BE example
python main.py   # from MegaDesk-Canvas/ — starts Supervisor BE on launch
```

### Hosted shell (`MegaDeskMember`)

Geometry and chrome are canvas-owned; the FE fills the host content parent via `FeSpec.build`.

| Owner | Responsibility |
| --- | --- |
| **Canvas** | Shell `child_window` (header, close, pos/size), selection ring, resize handles, world position, drag |
| **FE `build()`** | Widgets inside the host content parent only — no `dpg.window`, no standalone viewport |

The shell is one DPG subtree (header + content) under `canvas_window`. Closing via the header **x** leaves a placard (`data.gui_open = false`); double-click reopens (engine calls `open_megadesk_gui`).

### Canvas member persistence

Members are serialized into `canvas.json` (typically the repo-root file). Discriminator in JSON is **`type: "megadesk"`** (in-memory the host also keeps `global_guid = "megadesk"`).

| Field | Role |
| --- | --- |
| `canvas_id` | Instance GUID |
| `type` | Always `"megadesk"` |
| `nickname` | Display name (from `FeSpec.name`) |
| `node_name` | Discovery / FeSpec name |
| `position` | World `(x, y)` |
| `scale` | `[scale_x, scale_y]` — placard scale when the GUI is closed; open shells force scale to 1 and use `data.width` / `data.height` × zoom |
| `parents` / `children` | Serialized empty lists (legacy member-graph shape; unused). Layer membership lives under `hierarchy.layers[].children`. |
| `data` | FE payload: `width`, `height`, `gui_open`, `node_name`, … |

`icon` and Catalog black-square fallback live on `FeSpec`, not on the member record.

### Engine interaction (brief)

`DisplayEngine` drives selection, drag, resize, and drawlist placards/handles on `MegaDeskMember`. Lifecycle methods the host may call include `on_select` / `on_deselect`, drag/resize hooks, `on_create` / `on_destroy`, `draw` / `draw_resize_handles`, and `open_window` / `close_window`. Several drag/resize/create hooks are currently no-ops on the member; node authors should not rely on subclassing them — implement behavior inside `FeSpec.build` instead.

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
2. Add `<name>_node.py` with `get_exec_spec(mode)` returning `FeSpec` and/or `BeSpec`.
3. Register `[project.entry-points."MegaDesk.nodes"]`.
4. `pip install -e Nodes/<Name>` (add `[canvas]` / Dear PyGui if the node has an FE).
5. Restart MegaDesk so entry points are re-scanned (Supervisor BE starts with the canvas).
6. FE appears in Catalog; BE is launchable by endpoint once the Supervisor BE is alive.
