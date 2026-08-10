# MegaDesk FE canvas member (MegaDeskMember)

Canvas objects are MegaDesk FE shells backed by an `FeSpec`, not a thick
inheritance hierarchy. Geometry and chrome are canvas-owned; the FE fills the
host content parent via `FeSpec.build`.

## Fields

### Spec / type

| Field | Role |
| --- | --- |
| nickname / name | Display name (header + Catalog) |
| global_guid | Discriminator `"megadesk"` in `canvas.json` |
| node_name | FeSpec name / discovery key |
| description | Short blurb from the FeSpec |
| icon | Path on the FeSpec; empty/invalid → Catalog black square |

### Instance

| Field | Role |
| --- | --- |
| canvas_id | Instance GUID |
| position | World (x, y) |
| scale | Placard scale when GUI is closed |
| data.width / data.height | World-space content size; open shells render at `size * view_zoom` |
| parents / children | Serialized empty lists (legacy shape; unused) |
| data | FE payload (`width`, `height`, `gui_open`, `node_name`, …) |

## Interface (canvas hooks)

- `on_select` / `on_deselect`
- `on_start_drag` / `on_drag` / `on_end_drag`
- `on_start_resize` / `on_resize` / `on_end_resize`
- `on_create` / `on_destroy`
- `on_double_click` — reopen / focus hosted FE
- `draw` / `draw_resize_handles` — drawlist placard (closed) + selection/handles
- `open_window` / `close_window` — integrated shell lifecycle under `canvas_window`
