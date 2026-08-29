# VisionBoard

A sub-canvas inside one canvas node: sticky notes you can write on, containers
you can draw around them, and a camera you can pan and zoom. FE-only — it talks
to no Redis stream and launches no BE. What the board holds lives in the graph,
as this member's parameters.

## Controls

| Input | Effect |
|---|---|
| MB3 (right click) on empty board | Place a sticky, centred on the cursor |
| Double click a sticky | Type into it; the text wraps and shrinks to fit |
| Double click a container header | Rename the container |
| Left drag a sticky | Move it |
| Left drag a container header or border | Move it, carrying every sticky inside |
| Left drag the board | Pan |
| Mouse wheel | Zoom about the cursor (30%–300%) |
| `[ ]` button, then left drag | Draw a container; the tool disarms on release |

A container's inside is transparent and is not a grab target, so clicks pass
through to the stickies it holds — including MB3 to add another one.

## Halves

| Half | What it does |
|------|--------------|
| FE (`vision_board_frontend/app.py`) | Drawlist, mouse handlers, camera, inline text edit |
| BE | none |

## Parameters

Declared in `parameters.yaml`; **Capture** on the graph bar writes what is
currently on the board into the open graph.

| Name | Value |
|---|---|
| `NOTES` | JSON list of `{id, x, y, text}` |
| `CONTAINERS` | JSON list of `{id, x, y, w, h, name}` |

Coordinates are world units, independent of the camera: reopening the graph
restores the same layout at 100% zoom.

## Layout

`vision_board_frontend/board.py` is the model — stickies, containers, hit tests,
the camera transform, the note-text fitter, and the parameter codec. It imports
no Dear PyGui, so [`tests/test_vision_board.py`](../../tests/test_vision_board.py)
exercises the geometry without a desktop session. `app.py` owns the widgets and
does all the drawing; its geometry entry points (`place_note`, `press`, `drag`,
`release`, `open_editor`, `zoom`) take coordinates as arguments so the same
tests can drive them on a booted canvas.
