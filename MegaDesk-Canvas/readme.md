MegaDesk canvas — Dear PyGui whiteboard host for MegaDesk FE tools.

**Supervisor is built-in:** on launch the canvas starts the Supervisor BE via
`megadesk_contracts.ensure_supervisor_running()` (`python -m supervisor` from
this package). The operator UI is a right-hand pane
(`supervisor.panel.build_supervisor_panel`), docked like the Catalog and
collapsible the same way — Nodes and Logs tabs, not a Catalog / `MegaDesk.nodes`
entry. Managed BE logs land under worktree `Logs/{session}/{endpoint}.md`
(read `Logs/CURRENT`).

Install and run from this directory (after installing contracts):

```bash
conda activate MEGADESK
pip install -e ../MegaDesk-contracts
pip install -e .
python main.py
```

See [`Docs/node_protocol.md`](../Docs/node_protocol.md) for the node protocol
(`MegaDesk.nodes` / `FeSpec` / `BeSpec` / graph hosting).
Package layout and DPG chrome: [`docs/canvas.md`](docs/canvas.md).
Shared importable APIs live in the `megadesk-contracts` package (`MegaDesk-contracts/`).
Supervisor Redis packages: [`MegaDesk-contracts/redis/supervisor.md`](../MegaDesk-contracts/redis/supervisor.md).
Graphs live under `Graphs/` (default `default.json`); the graph bar loads any `.json`.
