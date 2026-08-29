# Notepad

A small pad on the canvas. Each note is a tab; the body is a text file
saved under `notes/` inside this node.

VoiceDeck writes the same documents the tabs show. The FE consumes
`NOTEPAD:CMD`; there is no backend. Tools are declared on
`get_tool_spec()` (`notepad_tools/`) so VoiceDeck discovers them.

## Halves

| Half | What it does |
|------|--------------|
| FE (`notepad_frontend/app.py`) | Tabs, editor, local `.txt` files |
| BE | none |

## Wire

Defined once in `megadesk_contracts.wire.notepad`. See
[`MegaDesk-Contracts/redis/notepad.md`](../../MegaDesk-Contracts/redis/notepad.md).

| Tool | Effect |
|------|--------|
| `create_note(title, text)` | New document, become the target |
| `add_note_text(text, title)` | Append to `title`, or to the current target |
| `switch_note(title)` | Change the target document |

## Files

Notes live under `NOTEPAD_ROOT`, or `Nodes/Notepad/notes/`.
