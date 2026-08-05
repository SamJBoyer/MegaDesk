# Supervisor

MegaDesk process lifecycle manager (BE) plus Dear PyGui operator panel (FE).

Both halves ship in this package and register a single `MegaDesk.nodes` entry
point. `get_exec_spec("FE")` / `get_exec_spec("BE")` keep them separate:

| Mode | What it returns |
|------|-----------------|
| `FE` | Operator panel (`frontend.app.build_ui`) — needs `[canvas]` |
| `BE` | Commander subprocess (`python -m commander`) |

Dropping **supervisor** on the MegaDesk canvas bootstraps the commander BE
automatically (see `megadesk.ensure_supervisor_running`). The commander never
manages its own BeSpec via `launch_node`.

## Setup

```bat
conda activate <MegaDesk-env>
pip install -e ../src
pip install -e .
pip install -e .[canvas]
```

## Run BE only

```bat
start_commander.bat
rem or: python -m commander
rem or: supervisor
```

## Run with MegaDesk canvas

1. `pip install -e .[canvas]` (and `../src` as above)
2. Start MegaDesk (`python main.py` from `src/`)
3. Drop-in sidebar → **supervisor** → place on canvas (BE starts on drop)
4. Double-click the placard for launch / stop / KILLALL controls

Or print FE instructions:

```bat
python -m frontend
```

## Smoke test

With Redis available and Plant installed (`pip install -e ../Plant`):

```bat
python -m commander.smoke_test
```

## Layout

- `commander/` — BE package (Pub/Sub server, process registry, Redis provision)
- `frontend/` — FE operator panel (`build_ui` for FeSpec)
- `supervisor_node.py` — `MegaDesk.nodes` → `get_exec_spec(mode)`
- `pyproject.toml` — packaging; optional `[canvas]` for Dear PyGui
