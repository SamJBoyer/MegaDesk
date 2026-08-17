Context: 

## 1. The problem

Plan for giving agents a way to exercise MegaDesk end-to-end through the real GUI, so that bugs at module interfaces get caught mechanically instead of by hand.

Most observed breakage is not inside a module — it is at the seam between two of them:
a field renamed on one side of a Redis stream, a consumer group that never acks, a
widget callback that stops firing after a lifecycle change. Unit tests on either side
of a seam pass while the seam is broken.

The seams in this workflow are all crossed either by a **Redis stream** or by a **GUI
callback**, so a test that cannot press buttons cannot reach them.

Because the canvas and every FE run in **one Python process** (see
[`Docs/node_protocol.md`](node_protocol.md) — FEs are `dpg.node` items inside the canvas
`node_editor`, not separate processes or windows), an agent does not need OS-level input
injection or pixel matching. It drives the same code a click would.

Currently, there is no API exposed by MegaDesk that would allow an agent to programatically control it. 

---

Command: 

Implement the following APIs 

| Capability | API | Notes |
|---|---|---|
| Address any widget | `megadesk::{canvas_id}::{suffix}` | Deterministic via `hosted_node_tag()`; every FE derives suffixes from `tag_prefix` |
| Read / write widget state | `dpg.get_value` / `dpg.set_value` | |
| Fire the real handler | `dpg.get_item_callback(tag)` then call it | Returns the actual bound callback, so tests run production code, not a reimplementation |
| Advance time deliberately | `dpg.render_dearpygui_frame()` in a controlled loop | Replaces `while dpg.is_dearpygui_running()` |
| See the screen | `dpg.output_frame_buffer(path)` | Writes a PNG an agent can read back |


Constraints: 


### Viewport must be shown, not minimized

`show_viewport(minimized=True)` renders nothing — `output_frame_buffer` produced a
79-byte empty PNG. Positioning the viewport off-screen instead works and produced a
real 46 KB render:

```python
dpg.create_viewport(width=1280, height=800, x_pos=-2400, y_pos=0)
dpg.setup_dearpygui()
dpg.show_viewport()
```

**Consequence:** this needs a desktop session. It is not headless and will not run on a
bare SSH session or a standard hosted CI runner without a virtual display.

Known bugs: 


## 3. Blockers found while verifying

Both are in `frame_pump`, both must be fixed before any GUI test can pass, because the
harness drops nodes onto an empty board — precisely the trigger condition.

### 3.1 Pump arms at an absolute frame that may already be past

```22:31:MegaDesk-contracts/megadesk_contracts/frame_pump.py
    def _pump() -> None:
        for cb in list(_callbacks):
            try:
                cb()
            except Exception:
                pass
        if dpg.is_dearpygui_running():
            dpg.set_frame_callback(dpg.get_frame_count() + 1, _pump)

    dpg.set_frame_callback(1, _pump)
```

The re-arm line is relative; the initial arm is the literal frame `1`. If the first
registration happens after frame 1 has rendered, the callback is scheduled for a frame
in the past and never fires — but `_armed` flips to `True`, so the pump is permanently
dead for the session. Since `_armed` and `_callbacks` are module globals shared by every
FE, **no** node on the board gets its per-frame drain. Background threads keep filling
`_ui_queue` and nothing empties it.

Measured: registering before the first frame gives 30 ticks over 30 frames; registering
at frame 30 gives **0 ticks**, forever.

**Live impact, independent of testing:** start the canvas with an empty board, drop your
first node, and that node — plus every node dropped afterward — silently never updates.
It is masked today only because the committed `canvas.json` has three members, so
`open_all_megadesk_guis()` registers at frame 0 during startup.

**Fix:** arm relative, `dpg.set_frame_callback(dpg.get_frame_count() + 1, _pump)`,
matching the re-arm line.

### 3.2 Module state outlives the DPG context

Cycling `create_context()` / `destroy_context()` three times in one process: DPG itself
is fine (frames render, values read back), but `_armed` stays `True` and `_callbacks`
accumulates `1 → 2 → 3`. Cycles 2 and 3 get **0 pump ticks**, and stale callbacks from
destroyed contexts stay registered forever, silently swallowed by the bare `except`.

**Fix:** add a `frame_pump.reset()` that clears `_callbacks` and `_armed`, called on
context teardown. Without it, tests must use one process per test.
