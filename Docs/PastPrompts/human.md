You are a professional engineer specializing in using python and Dear PyGui.

Our job is to prototype a digital whiteboard that hosts custom interactive GUIs
so we can organize complicated software projects. The main feature is an
infinitely scrolling canvas and an engine that renders MegaDesk FE tools
discovered via `MegaDesk.nodes` / `FeSpec`.

We assume all FEs are written in Python with Dear PyGui and expose a `FeSpec`
(see `plugins.md` and `Nodes.MD`). FE tools fill a host-owned shell under the
master canvas - they do not run as stand-alone Dear PyGui apps.

canvas.json: canvas state is saved as JSON. Structure is in `root.md`. Top-level
field is `members` (every object on the canvas). Each object gets its own GUID.
When an FE is dropped onto the canvas it is added to `members`.

Display Engine: parses `canvas.json` and displays MegaDesk members on the board.

Layout:

- Catalog sidebar (left): menu of FE tools you can drag onto the canvas.

Features:

| NAV-1 | Canvas pans by holding right-click and moving the mouse. | Must |
| NAV-2 | ~~Mouse scroll zooms the canvas in and out; open subGUIs shrink and scale with zoom.~~ Removed (display engine migrating to DPG node_editor). | Removed |
| NAV-3 | Hovering an object and clicking MB1 selects it. | Must |
| NAV-4 | Holding MB1 on an object and moving the mouse drags it. | Must |
| NAV-5 | A single right-click opens a context menu. | Must |
| NAV-6 | Pressing Delete on a selected object deletes it. | Must |

### Markup / chrome

| ID | Requirement | Priority |
| --- | --- | --- |
| UI-1 | Infinite scrolling canvas as the primary workspace. | Must |
| UI-2 | Collapsible Catalog sidebar to drag FE tools onto the canvas. | Must |
| UI-5 | ~~Layer control with visibility and lock toggles.~~ Removed (members-only canvas; no hierarchy.layers). | Removed |
