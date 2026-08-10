MegaDesk canvas — Dear PyGui whiteboard host for MegaDesk FE tools.

Install and run from this directory (after installing contracts):

```bash
conda activate <MegaDesk-env>
pip install -e ../MegaDesk-contracts
pip install -e .
python main.py
```

See `docs/plugins.md` for the plugin contract (`MegaDesk.nodes` / `FeSpec`).
Shared importable APIs live in the `megadesk-contracts` package (`MegaDesk-contracts/`).
