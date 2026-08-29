# Notepad

A small tabbed pad on the canvas. Each note is a tab and a ``.txt`` file.
Point it at a GitHub repo and Save writes the files, git-includes them, and
pushes. VoiceDeck can create documents, append text, and switch the target tab.

## Halves

| Half | What it does |
|------|--------------|
| FE (`notepad_frontend/app.py`) | Tabs, editor, clone/save |
| BE | none |

## Parameters

| Name | Value |
|---|---|
| `GIT_URL` | GitHub (or any git) URL notes are saved to |

## Wire

`NOTEPAD:COMMAND` — `action` is `create` / `append` / `switch`. Defined in
`megadesk_contracts.wire.notepad`. Voice tools: `new_document`, `add_text`,
`switch_document`.

`notepad_frontend/pad.py` is the model — documents, ``.txt`` files, git include.
It imports no Dear PyGui.
