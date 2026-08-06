# MegaDesk canvas plugins

MegaDesk is a Dear PyGui whiteboard host (package root: `src/`).

Canvas members are **MegaDesk productivity nodes** (TicketDispatcher, MergeManager, …).
They use the thin `FeSpec` contract from the shared `megadesk` package and register via
`MegaDesk.nodes` entry points.

Install everything into the same MegaDesk conda env.

## Install

```bash
conda activate <MegaDesk-env>
pip install -e .
pip install -e ../Nodes/TicketDispatcher
pip install -e ../Nodes/MergeManager
python main.py
```

## MegaDesk.nodes

Each tool exposes `get_exec_spec(mode)` where `mode` is `"FE"` or `"BE"`:

```python
from megadesk import FeSpec, Mode

def get_exec_spec(mode: Mode):
    if mode == "FE":
        return FeSpec(
            name="my_tool",
            description="…",
            icon=None,
            default_width=480,
            default_height=420,
            build=build_ui,  # fills host content parent — see contract below
        )
    return None  # FE-only
```

```toml
[project.entry-points."MegaDesk.nodes"]
my_tool = "my_tool.node:get_exec_spec"
```

### Canvas-integrated FE shell

MegaDesk discovers FE specs at startup and shows them in **Catalog**. Dropping a
tool onto the canvas places a MegaDesk member (`type: "megadesk"` + `node_name`
in `canvas.json`) and builds its UI into a **host-owned shell** under
`canvas_window`:

| Owner | Responsibility |
| --- | --- |
| **Canvas** | Shell `child_window` (header, close, pos/size), selection ring, resize handles, world position, drag |
| **FE `build()`** | Widgets inside the host content parent only — no `dpg.window`, no standalone viewport |

`build` signature:

```python
def build_ui(parent: str, *, tag_prefix: str, width: int, height: int) -> None:
    ...
```

The shell is one DPG subtree (header + content). Pan/zoom updates shell `pos`
and size (`world * zoom`) so open subGUIs shrink and scale with the canvas.
Closing via the header **x** leaves a placard (`data.gui_open = false`);
double-click reopens. Open/closed is restored from `canvas.json` on load.

Store a cleanup callable on the content parent with
`dpg.set_item_user_data(parent, close_fn)` so the host can shut the FE down
when collapsing to a placard or deleting the member.

If the same entry point also returns a `BeSpec` for `"BE"`, MegaDesk `XADD`s
Redis `LAUNCHREQUEST` with `node_endpoint` = the node name (and `parameters=""`)
so Supervisor can start the backend process.

**Supervisor** is the exception: dropping its FE bootstraps the Supervisor BE from
its own `BeSpec` (`megadesk.ensure_supervisor_running`) because `LAUNCHREQUEST`
requires the Supervisor BE to already be running. Install with
`pip install -e ../Nodes/Supervisor[canvas]`.
