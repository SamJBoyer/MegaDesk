# Hosted FE detach / orphan bug

Status: fixed (canvas host lifecycle + sync).  
Related: `plugins.md` (hosted FE shell), `parent_gui_class.md` (`MegaDeskMember`).

## Symptoms

1. The white tool panel (Ticket Dispatcher, Supervisor, …) visually **detaches** from the blue canvas header / placard — offset after drag, pan, or zoom.
2. After **deleting** the canvas member (or collapsing via the chrome **x**), the white panel sometimes **remains** on screen.
3. Those leftover panels are often **unresponsive**: no title-bar close, `no_move`, and no canvas chrome targeting them.

## Architecture (why this can happen at all)

MegaDesk does **not** parent the FE widgets inside a Dear PyGui container node.

| Layer | What it is | Owner |
| --- | --- | --- |
| Member / placard | World position, size, `gui_open` | `MegaDeskMember` + `canvas.json` |
| Header chrome | Drawlist rectangle, label, close affordance, selection ring | `MegaDeskMember.draw()` into `canvas_drawlist` |
| Content panel | Top-level DPG `window` (`no_title_bar`, `no_move`, `no_resize`) | FE `FeSpec.build()` |

The content panel is a **separate top-level window**. Every frame, `DisplayEngine.sync_megadesk_windows()` push-syncs its screen position under the header:

```text
global_pos = drawlist_origin + world_to_screen(member.position) + (0, HEADER_H)
```

The only runtime link between chrome and panel was the in-memory string `_window_tag` (deterministic form `megadesk::{canvas_id}`).

`parents` / `children` in `canvas.json` are legacy serialization fields and are **unused** for this shell. This was never a scene-graph reparent bug.

## Root causes

### 1. Tag cleared while the DPG window still lived (primary)

Several paths could set `_window_tag = None` (and `gui_open = false`) **without** `dpg.delete_item` on the hosted window:

- FE `on_close` / `user_data` cleanup callbacks that only updated host state
- Sync treating a failed `does_item_exist(tag)` as “GUI closed” and dropping the binding

After that desync:

- Chrome drew as a **closed placard** (or stopped tracking the open shell) and still moved with pan/drag
- The live white window was **no longer in the sync loop** (`if not tag: continue`)
- Result: classic detach — placard in one place, panel in another

### 2. Delete only cleaned through `_window_tag`

`CanvasModel.delete_node` → `on_destroy` → `close_window` only deleted when `_window_tag` was set and `does_item_exist` succeeded.

If the binding was already cleared (cause 1), `close_window` was effectively a no-op: the member disappeared from the model, but `megadesk::{canvas_id}` stayed in the DPG context.

### 3. Why orphans felt “dead”

Hosted panels are created with `no_move` / `no_title_bar` / no DPG close control — canvas chrome owns move and close. An orphan has:

- No per-frame position sync
- No member to hit-test for Delete / chrome **x**
- Often FE `shutdown()` already run (frame pump unregistered) if cleanup ran without delete

So the panel could remain visible but ignore interaction.

### 4. Per-frame hover `select()` (amplifier)

While the cursor hovered a content panel, sync called `select()` **every frame**. `select()` always `redraw()`s the entire drawlist. That churn made chrome vs panel drift more visible under load and fought the push-sync.

## Fix

Code: `engine/megadesk_member.py`, `engine/display_engine.py`.

### Lifecycle

- `hosted_window_tag(canvas_id)` — single deterministic tag for open / close / delete / sync
- `destroy_hosted_window(tag)` — run `user_data` cleanup, then **always** delete if the item still exists
- `close_window()` — destroys by `_window_tag` **or** the deterministic tag (cleared binding can no longer skip delete)
- `open_window()` — wraps FE `user_data` so cleanup that runs alone still deletes the window; failed `build()` clears the host binding instead of leaving a fake “open” shell
- `bind_existing_window()` — reclaim a live panel when the binding was dropped but the DPG item remains

### Sync

- If `_window_tag` is missing but `megadesk::{id}` exists and the member still wants the GUI open → **reclaim** and keep syncing
- If a panel exists with no host intent → **destroy** the orphan
- Position push uses `configure_item(pos=…, width=…, height=…)` with verify/retry via `set_item_pos` when ImGui drifts
- Layer visibility hides/shows hosted windows (chrome was already gated; panels were not)
- Hover selection runs only when the hovered member is not already selected (no per-frame redraw storm)

### Delete

- After `delete_node`, explicitly `destroy_hosted_window(hosted_window_tag(cid))` so a stale binding cannot leave a panel behind

## How to verify

1. Drop a Catalog tool; confirm header chrome sits flush above the white panel.
2. Drag the header, pan (RMB), zoom (wheel) — panel must stay glued under the header.
3. Close via chrome **x** — panel gone, placard remains; double-click reopens aligned.
4. Delete the member — chrome **and** panel both gone; no floating orphan.
5. Toggle layer visibility — hosted panel hides/shows with the layer.

## Non-goals / still true

- Hosted FEs remain top-level DPG windows glued by push-sync, not true DPG children of the drawlist.
- Pixel size does not scale with canvas zoom (by design).
- FE tools must keep storing a cleanup callable on the window (`dpg.set_item_user_data`) so the host can shut services down when collapsing or deleting.
