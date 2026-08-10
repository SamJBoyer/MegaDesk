MegaDesk canvas — Dear PyGui whiteboard host for MegaDesk FE tools.

**Supervisor is built-in:** on launch the canvas starts the Supervisor BE via
`megadesk_contracts.ensure_supervisor_running()` (`python -m supervisor` from
this package). The operator UI is collapsible chrome
(`supervisor.panel.build_supervisor_panel`), not a Catalog / `MegaDesk.nodes`
entry. Managed BE logs land under `logs/<endpoint>/<unique_id>.log`.

Install and run from this directory (after installing contracts):

```bash
conda activate <MegaDesk-env>
pip install -e ../MegaDesk-contracts
pip install -e .
python main.py
```

See [`Docs/node_protocol.md`](../Docs/node_protocol.md) for the node protocol
(`MegaDesk.nodes` / `FeSpec` / `BeSpec` / canvas hosting).
Shared importable APIs live in the `megadesk-contracts` package (`MegaDesk-contracts/`).
Supervisor Redis packages: [`MegaDesk-contracts/redis/supervisor.md`](../MegaDesk-contracts/redis/supervisor.md).
