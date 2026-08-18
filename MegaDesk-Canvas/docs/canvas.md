# MegaDesk-Canvas

Package-local notes. Discovery, `FeSpec` / `BeSpec`, hosted-shell contract, graph member fields, Supervisor streams, and logging are in [`Docs/node_protocol.md`](../../Docs/node_protocol.md). Install/run: [`readme.md`](../readme.md).

## Layout

```text
MegaDesk-Canvas/
  main.py                  # viewport, build_canvas(), render loop
  engine/
    display_engine.py      # Catalog drop, node_editor, Delete, BE launch on drop
    megadesk_member.py     # native dpg.node host around FeSpec.build
    graph_model.py         # Graphs/*.json load/save; GraphError
    graph_bar.py           # pick / save / save-as / Capture / delete
    megadesk_registry.py   # in-process FE catalog
    icons.py
  supervisor/              # Canvas-owned BE (`python -m supervisor`) + collapsible panel
```

`build_canvas(model, …)` is the construction seam shared by `main()` and the integration harness. `main()` sets `MEGADESK_CANVAS_ROOT`, starts Supervisor, loads the graph (empty board on `GraphError`), runs the loop, then `model.save()` and `frame_pump.reset()`.

## Chrome (DPG tags)

| Tag | Role |
| --- | --- |
| `graph_window` | Primary window (no title bar) |
| `graph_bar` | 32px bar above Catalog + editor |
| `canvas_body` | Horizontal row: Catalog + editor + Supervisor |
| `catalog_sidebar` | Left Catalog pane; collapsible |
| `catalog_sidebar::toggle` | Collapse / expand Catalog |
| `catalog_sidebar::body` | Catalog palette; drag payload type `MEGADESK_NODE`, key `megadesk:<name>` |
| `graph_editor_host` | Center pane wrapping the node editor |
| `graph_editor` | `dpg.node_editor` |
| `graph_ref_node` | Hidden node used to map screen mouse → editor-grid on drop |
| `supervisor_panel_window` | Right Supervisor pane; collapsible (not a Catalog entry) |
| `supervisor_panel_window::toggle` | Collapse / expand Supervisor |
| `supervisor_panel_window::body` | Supervisor tab contents |
| `supervisor_panel_window::tabs` | Nodes / Logs tab bar |

Hosted FE tags: `megadesk::{member_id}` (the `dpg.node`) and `megadesk::{member_id}::content` (`tag_prefix` passed to `FeSpec.build`).
