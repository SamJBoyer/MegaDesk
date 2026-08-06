# MegaDesk Nodes

A **node** is a modular tool inside MegaDesk. Nodes can expose a front-end (FE), a back-end (BE), or both. Discovery and launch are driven by packaging entry points — not by YAML manifests or hard-coded paths.

| Half | What it is | Who uses it |
|------|------------|-------------|
| **FE** | Always Dear PyGui. Can be dropped onto the MegaDesk canvas as a subgui / subnode. | MegaDesk canvas (`src/`) via `get_exec_spec("FE")` → `FeSpec` |
| **BE** | Long-lived process (argv + optional cwd). Managed as a subprocess. | Supervisor commander via `get_exec_spec("BE")` → `BeSpec` |

Shared contract lives in the installable `megadesk` package (shipped under `src/`): `FeSpec`, `BeSpec`, entry-point discovery, and the Supervisor Redis client.

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
3. Depend on `megadesk` so the node can import `FeSpec` / `BeSpec` / `Mode`.
4. FE-only, BE-only, and FE+BE are all valid. Return `None` for the mode you do not support.
5. `get_exec_spec` must return a ready-to-use launch description — the caller should not need extra setup beyond what the installed package already provides.

**When to split FE / BE** (from the product README):

1. Stateful things should.
2. Processes that launch or manage other processes should.
3. Stateless tools usually should not.

---

## Entry point registration

```toml
[project.entry-points."MegaDesk.nodes"]
my_tool = "my_tool_node:get_exec_spec"
```

- The **entry-point name** (`my_tool`) is the package’s discovery key.
- Prefer making `FeSpec.name` / `BeSpec.name` match that key so FE drop and BE `launch_node` use the same nickname.
- One entry point serves both modes; branching happens inside `get_exec_spec`.

Examples in-repo:

| Node | Entry point | Modes |
|------|-------------|-------|
| Supervisor | `supervisor` | FE + BE |
| Plant | `plant` | BE only |
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
| `icon` | `str \| None` | Path to an icon image, or `None` |
| `default_width` | `int` | Initial window width |
| `default_height` | `int` | Initial window height |
| `build` | callable | Builds the Dear PyGui UI |

`build` signature (convention):

```text
(tag, *, pos=None, on_close=None, width=…, height=…, no_move=…, no_resize=…) -> window_tag
```

FE-only example pattern:

```python
from megadesk import FeSpec, Mode

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

Back-end launch instruction for Supervisor:

| Field | Type | Role |
|-------|------|------|
| `name` | `str` | Managed nickname (`launch_node` body) |
| `argv` | `list[str]` | Full process argv |
| `cwd` | `str \| None` | Working directory (often the node package root) |

Launch contract:

```text
subprocess.Popen(BeSpec.argv, cwd=BeSpec.cwd)
```

BE-only example pattern:

```python
from megadesk import BeSpec, Mode

def get_exec_spec(mode: Mode):
    if mode == "BE":
        return BeSpec(
            name="plant",
            argv=[sys.executable, "-u", "-m", "PlantManager"],
            cwd=str(package_root),
        )
    return None
```

FE+BE example: return `FeSpec` for `"FE"` and `BeSpec` for `"BE"` from the same function (see `Nodes/Supervisor/supervisor_node.py`).

---

## Discovery API (`megadesk`)

Installed entry points are scanned via `importlib.metadata` group `MegaDesk.nodes`.

| Function | Returns |
|----------|---------|
| `discover_frontends()` | `dict[str, FeSpec]` — every node that returns an `FeSpec` for `"FE"` |
| `discover_backends()` | `dict[str, BeSpec]` — every node that returns a `BeSpec` for `"BE"` |
| `load_exec_spec(name, mode)` | One entry point’s result for that mode |
| `get_backend(name)` | `BeSpec \| None` by nickname |
| `has_backend(name)` | Whether a BE exists for that name |

Keys prefer `spec.name`, falling back to the entry-point name.

---

## How the FE uses nodes (MegaDesk canvas)

1. On startup, the canvas calls `discover_frontends()` (via `engine.megadesk_registry`) and fills the Catalog palette (`megadesk:<name>`).
2. Dropping a node places a canvas member (`type: "megadesk"`, `node_name` in `canvas.json`) and opens the Dear PyGui window via `FeSpec.build` at the drop point.
3. Pan/zoom keeps the window world-anchored; close leaves a placard; double-click reopens the GUI.
4. If `has_backend(node_name)` is true after drop:
   - **Supervisor exception:** `ensure_supervisor_running()` spawns the Supervisor `BeSpec` directly (`python -m commander`) and waits for `GBD:COMMANDER:ALIVE`. The commander cannot `launch_node` itself because it is not up yet.
   - **Every other BE node:** publish Redis `launch_node:<caller_identity>` with body = node name so the commander starts that node’s `BeSpec`.

Canvas-side install:

```bash
conda activate <MegaDesk-env>
pip install -e src
pip install -e Nodes/TicketDispatcher   # FE example
pip install -e Nodes/MergeManager       # FE example
pip install -e Nodes/Supervisor[canvas] # FE + BE
python main.py   # from src/
```

New productivity tools use `MegaDesk.nodes` + `FeSpec`. See `src/docs/plugins.md`.

---

## How the BE uses nodes (Supervisor)

1. The commander refreshes backends with `discover_backends()` (excluding its own Supervisor `BeSpec` so it never `launch_node`s itself).
2. On `launch_node:<identity>` with body `<name>`, it resolves `get_backend(name)` and runs `Popen(argv, cwd=…)`, tracking the process by `BeSpec.name`.
3. `stop_node` / `KILLALL` tear down managed processes. See `contracts/redis/supervisor.md`.

Typical Redis path after an FE drop that also has a BE:

1. Caller subscribes to `acknowledgements:<identity>`
2. Publishes `launch_node:<identity>` with the node name
3. Waits for `SUCCESS` / `FAILED`

---

## Minimal checklist for a new node

1. Create `Nodes/<Name>/` with `pyproject.toml` depending on `megadesk`.
2. Add `<name>_node.py` with `get_exec_spec(mode)` returning `FeSpec` and/or `BeSpec`.
3. Register `[project.entry-points."MegaDesk.nodes"]`.
4. `pip install -e Nodes/<Name>` (add `[canvas]` / Dear PyGui if the node has an FE).
5. Restart MegaDesk / Supervisor so entry points are re-scanned.
6. FE appears in Catalog; BE is launchable by nickname once the commander is alive.
