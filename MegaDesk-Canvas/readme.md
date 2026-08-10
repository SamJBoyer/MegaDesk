MegaDesk canvas — Dear PyGui whiteboard host for MegaDesk FE tools.

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
