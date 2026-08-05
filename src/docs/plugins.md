# MegaDesk canvas plugins

MegaDesk is a Dear PyGui whiteboard host (package root: `src/`).

- **Built-in ideation tools** (`nodes/sticky`, `nodes/container`) use the thick
  `BaseNode` contract.
- **MegaDesk productivity nodes** (TicketDispatcher, MergeManager, …) use the
  thin `FeSpec` contract from the shared `megadesk` package and register via
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

## MegaDesk.nodes (preferred for tools)

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
            build=build_ui,  # hosted content panel — see contract below
        )
    return None  # FE-only
```

```toml
[project.entry-points."MegaDesk.nodes"]
my_tool = "my_tool.node:get_exec_spec"
```

### Canvas-hosted FE shell

MegaDesk discovers FE specs at startup and shows them in Drop-in. Dropping a
tool onto the canvas places a MegaDesk member (`type: "megadesk"` + `node_name`
in `canvas.json`) and opens its Dear PyGui UI as a **hosted content panel**:

| Owner | Responsibility |
| --- | --- |
| **Canvas** | Header chrome, selection ring, resize handles, world position, drag |
| **FE `build()`** | Widgets inside a fixed panel (`no_title_bar`, `no_move`, `no_resize`) |

`build` signature for hosting:

```python
def build_ui(
    tag,
    *,
    pos=None,
    on_close=None,
    width=…,
    height=…,
    no_move=True,
    no_resize=True,
    no_title_bar=True,
) -> str:  # window tag
    ...
```

Push sync every frame glues the panel under the canvas header (pixel-sized;
does not scale with zoom). Closing via the chrome **x** leaves a placard
(`data.gui_open = false`); double-click reopens. Open/closed is restored from
`canvas.json` on load.

Store a cleanup callable on the window with `dpg.set_item_user_data(tag, close_fn)`
so the host can shut the FE down when collapsing to a placard.

If the same entry point also returns a `BeSpec` for `"BE"`, MegaDesk publishes
Redis `launch_node:<identity>` with the node name so Supervisor can start the
backend process.

**Supervisor** is the exception: dropping its FE bootstraps the commander from
its own `BeSpec` (`megadesk.ensure_supervisor_running`) because `launch_node`
requires the commander to already be running. Install with
`pip install -e ../Nodes/Supervisor[canvas]`.

## Built-in BaseNode (sticky / container)

Built-ins still subclass `BaseNode` and self-register under `nodes/`.

```python
from executive import BaseNode, register

@register
class MyToolNode(BaseNode):
    nickname = "My Tool"
    global_guid = "my_tool"
    description = "What this tool does."

    def draw(self, drawlist, world_to_screen, selected: bool = False) -> None:
        ...
```

| Import | Purpose |
| --- | --- |
| `from executive import BaseNode` | Thick parent for built-in canvas objects |
| `from executive import register` | Register a built-in type |

Activation: `on_double_click` (or sticky text edit). You **must** implement
`draw(...)`.

## Legacy `executive.nodes`

External `BaseNode` plugins via `[project.entry-points."executive.nodes"]` are
still loaded for compatibility. Prefer `MegaDesk.nodes` + `FeSpec` for new tools.

## Sample BaseNode tool (legacy)

```bash
pip install -e .
pip install -e examples/sample_canvas_tool[canvas]
python main.py
```
