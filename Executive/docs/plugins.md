# Executive canvas plugins

Executive is a Dear PyGui whiteboard host.

- **Built-in ideation tools** (`nodes/sticky`, `nodes/container`) use the thick
  `BaseNode` contract.
- **MegaDesk productivity nodes** (TicketDispatcher, MergeManager, …) use the
  thin `FeSpec` contract from the shared `megadesk` package and register via
  `MegaDesk.nodes` entry points.

Install everything into the same MegaDesk conda env.

## Install

```bash
conda activate <MegaDesk-env>
pip install -e ../megadesk
pip install -e .
pip install -e ../TicketDispatcher
pip install -e ../MergeManager
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
            default_width=220,
            default_height=140,
            build=build_ui,  # (tag, *, pos=None, on_close=None) -> window_tag
        )
    return None  # FE-only
```

```toml
[project.entry-points."MegaDesk.nodes"]
my_tool = "my_tool.node:get_exec_spec"
```

Executive discovers FE specs at startup, shows them in Drop-in, places a thin
placard on drop (`type: "megadesk"` + `node_name` in `canvas.json`), and opens
`build()` on double-click.

If the same entry point also returns a `BeSpec` for `"BE"`, Executive publishes
Redis `launch_node:<identity>` with the node name so Supervisor can start the
backend process.

**Supervisor** is the exception: dropping its FE bootstraps the commander from
its own `BeSpec` (`megadesk.ensure_supervisor_running`) because `launch_node`
requires the commander to already be running. Install with
`pip install -e ../Supervisor[canvas]`.

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
