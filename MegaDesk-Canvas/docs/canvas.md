# MegaDesk-Canvas

Package-local notes. Discovery, `FeSpec` / `BeSpec`, hosted-shell contract, graph member fields, Supervisor streams, and logging are in [`Docs/node_protocol.md`](../../Docs/node_protocol.md). Install/run: [`readme.md`](../readme.md).

## Layout

```text
MegaDesk-Canvas/
  main.py                  # viewport, build_canvas(), render loop
  canvas_node.py           # tools-only MegaDesk.nodes entry (no FE / BE)
  canvas_tools/            # VoiceDeck ToolSpec: list / select / type / click
  engine/
    display_engine.py      # Catalog drop, node_editor, Delete, BE launch on drop
    megadesk_member.py     # native dpg.node host around FeSpec.build
    graph_model.py         # Graphs/*.json load/save; GraphError
    graph_bar.py           # pick / save / save-as / Capture / delete
    megadesk_registry.py   # in-process FE catalog
    canvas_api.py          # in-process NodeDriver verbs; sync_members drains CANVAS:CMD
    icons.py
  supervisor/              # Canvas-owned BE (`python -m supervisor`) + collapsible panel
  voice_deck/              # Canvas-owned VoiceDeck chrome panel + singleton BE launch
```

`build_canvas(model, …)` is the construction seam shared by `main()` and the integration harness. `main()` sets `MEGADESK_CANVAS_ROOT`; unless `DEV_FLUSH_MODE` is explicitly off (`0` / `false` / `no` / `off`, case-insensitive; default on) it FLUSHDB's live Redis DB 0 then DB 1 (`flush_live_redis_pair`) so the new supervisor recreates consumer groups and hashes on a fresh pair; then starts Supervisor, launches the VoiceDeck BE once (`ensure_voice_deck_running`), loads the graph (empty board on `GraphError`), runs the loop, then `model.save()`, VoiceDeck panel shutdown, and `frame_pump.reset()`. Disable with `set DEV_FLUSH_MODE=0` (Windows) or `export DEV_FLUSH_MODE=0` before starting canvas — no GUI chrome. Flush failure is logged; canvas continues. `python -m supervisor` alone does not flush.

Boot opens the last graph recorded in `Graphs/CURRENT` when that file still points at a valid graph; otherwise `Graphs/default.json`. Switching or Save As updates the pointer.

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
| `voice_deck_panel_window` | Bottom VoiceDeck pane; collapsible (not a Catalog entry) |
| `voice_deck_panel_window::toggle` | Collapse / expand VoiceDeck |
| `voice_deck_panel_window::body` | VoiceDeck controls + transcript |

Hosted FE tags: `megadesk::{member_id}` (the `dpg.node`) and `megadesk::{member_id}::content` (`tag_prefix` passed to `FeSpec.build`).

## Voice tools

`get_tool_spec()` on `canvas_node` offers VoiceDeck the same verbs the
integration harness uses: `list_nodes`, `drop_node`, `select_node`,
`list_widgets`, `get_widget`, `type_into`, `click_widget`, `select_widget`.
Handlers publish `CANVAS:CMD`; `CanvasApi` applies them through the live
widget callbacks and replies on `CANVAS:REPLY`. Wire:
[`MegaDesk-Contracts/redis/canvas.md`](../../MegaDesk-Contracts/redis/canvas.md).
