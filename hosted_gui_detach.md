# Hosted FE detach / orphan bug

Status: **superseded** by canvas-integrated shells (see `plugins.md`).

The old design used a top-level DPG content window push-synced under drawlist
header chrome. That split caused detach/orphan bugs. MegaDesk now builds each
open FE into a single host-owned `child_window` shell under `canvas_window`
(header + content). Node FEs no longer create their own windows or run
stand-alone.

Historical root-cause notes below are kept for archaeology only.

---

## Symptoms (legacy)

1. The white tool panel visually **detached** from the blue canvas header.
2. After deleting/collapsing, the white panel sometimes **remained**.
3. Leftover panels were often **unresponsive**.

## Legacy architecture

| Layer | What it was |
| --- | --- |
| Header chrome | Drawlist rectangle / label / close |
| Content panel | Separate top-level `dpg.window` glued by push-sync |

## New architecture

| Layer | Owner |
| --- | --- |
| Shell `child_window` under `canvas_window` | Host (pos, size, header, close) |
| Content widgets | FE `FeSpec.build(parent, …)` |
| Drawlist | Grid, selection ring, resize handles, closed placards |
