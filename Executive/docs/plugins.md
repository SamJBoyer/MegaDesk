# Executive canvas plugins

Executive is a Dear PyGui whiteboard host. Built-in nodes live under `nodes/`.
External productivity tools live in their own repos, implement the full
`BaseNode` contract, and register into the canvas through pip entry points.

**Always run Executive in the `loot` conda environment.** Install tools into
that same environment.

## Install Executive into loot

```bash
conda activate loot
cd /path/to/Executive
pip install -e .
python main.py
```

## Public contract

Every canvas GUI—built-in or external—subclasses `BaseNode` and is registered
with the host registry.

```python
from executive import BaseNode, register

@register
class MyToolNode(BaseNode):
    nickname = "My Tool"
    global_guid = "my_tool"  # stable type id written to canvas.json
    description = "What this tool does."

    def draw(self, drawlist, world_to_screen, selected: bool = False) -> None:
        ...
```

Stable imports (prefer the `executive` facade):

| Import | Purpose |
| --- | --- |
| `from executive import BaseNode` | Parent class every node must subclass |
| `from executive import register` | Decorator / function to register a type |

Equivalent: `from engine.base_node import BaseNode` and
`from engine.registry import register`.

### Required type metadata

| Field | Role |
| --- | --- |
| `nickname` | Sidebar label and default header |
| `global_guid` | Stable type id persisted as `members[].type` |
| `description` | Tooltip / help text in the Drop-in panel |
| `icon` | Image path for the Drop-in grid (absolute, CWD-relative, or relative to the node module). Empty or invalid → default black square |
| `is_container` | Spatial frame flag (`False` by default). Set `True` so contained nodes become children and move with this node |
| `default_width` / `default_height` | Initial world size |
| parent/child limit fields | Optional hierarchy constraints |

### Instance / persistence

Instance state is owned by `BaseNode` (`canvas_id`, `position`, `scale_x` /
`scale_y`, `parents`, `children`, `data`). Put tool-private state in `data`;
it is saved and loaded with `canvas.json`.

### Hooks the host invokes

Implement what you need; defaults are no-ops except where noted in
`engine/base_node.py`:

- Selection / drag / resize: `on_select`, `on_deselect`, `on_start_drag`,
  `on_drag`, `on_end_drag`, `on_start_resize`, `on_resize`, `on_end_resize`
- Lifecycle: `on_create`, `on_destroy`
- Containment: `on_object_enter`, `on_object_exit`
- Activation: `on_double_click` (use this to open tool-owned Dear PyGui windows)

You **must** implement `draw(drawlist, world_to_screen, selected=...)`.

Reference implementations: `nodes/sticky/node.py`, `nodes/container/node.py`.

## Expose a tool via pip

In the tool’s `pyproject.toml`:

```toml
[project.optional-dependencies]
canvas = ["dearpygui>=2.0"]

[project.entry-points."executive.nodes"]
my_tool = "my_tool.canvas_node:MyToolNode"
```

The entry point must resolve to:

1. a `BaseNode` subclass, or
2. a callable that returns one subclass or a sequence of subclasses

Install into loot (Executive must already be installed there):

```bash
conda activate loot
pip install -e /path/to/Executive
pip install -e ../my-tool[canvas]
python /path/to/Executive/main.py
```

Restart the canvas after installing or updating plugins. Entry points are
discovered once at startup by `discover_nodes()`.

If a canvas member’s `type` GUID is not registered (tool not installed), that
member is skipped on load.

## Sample tool

This repo includes a minimal external package:

```bash
conda activate loot
pip install -e .
pip install -e examples/sample_canvas_tool[canvas]
python main.py
```

You should see **Sample Tool** in the sidebar. Place it, double-click to edit
its label, save the canvas, and reload to confirm persistence.
