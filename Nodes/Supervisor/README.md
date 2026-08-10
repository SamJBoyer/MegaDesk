# Supervisor

MegaDesk process lifecycle manager (BE) plus Dear PyGui operator panel (FE).

Both halves ship in this package and register a single `MegaDesk.nodes` entry
point. `get_exec_spec("FE")` / `get_exec_spec("BE")` keep them separate:

| Mode | What it returns |
|------|-----------------|
| `FE` | Operator panel (`supervisor_frontend.app.build_ui`) — needs `[canvas]` |
| `BE` | Supervisor subprocess (`python -m backend`) |

Dropping **supervisor** on the MegaDesk canvas bootstraps the Supervisor BE
automatically (see `megadesk_contracts.ensure_supervisor_running`). The BE never
manages its own BeSpec via `LAUNCHREQUEST`.

## Setup

```bat
conda activate <MegaDesk-env>
pip install -e ../../MegaDesk-contracts
pip install -e ".[canvas]"
```

## Run BE only

```bat
start_supervisor.bat
rem or: python -m backend
rem or: supervisor
```

## Run with MegaDesk canvas

1. `pip install -e .[canvas]` (and MegaDesk-contracts as above)
2. Start MegaDesk (`python main.py` from `MegaDesk-Canvas/`)
3. Catalog sidebar → **supervisor** → place on canvas (BE starts on drop)
4. Double-click the placard for Catalog Send / Running Stop controls

Or print FE instructions:

```bat
python -m supervisor_frontend
```

## Smoke test

With Redis available and Plant installed (`pip install -e ../Plant`):

```bat
python -m backend.smoke_test
```

## Layout

- `backend/` — BE package (stream consumer, process registry, Redis provision)
- `supervisor_frontend/` — FE operator panel (`build_ui` for FeSpec)
- `supervisor_node.py` — `MegaDesk.nodes` → `get_exec_spec(mode)`
- `pyproject.toml` — packaging; optional `[canvas]` for Dear PyGui

## Redis control plane

See `MegaDesk-contracts/redis/supervisor.md`:

- `LAUNCHREQUEST` stream — launch a discovered BE (`node_endpoint`, `parameters`)
- `KILLREQUEST` stream — stop one instance (`node_endpoint`, `unique_id`)
- `NODEEXIT` stream — natural exit metadata (not log bodies)
- `RUNNINGNODES:<unique_id>` hash — registry (`status`, PID, `log_path`, exit fields)
- `GBD:SUPERVISOR:ALIVE` — BE heartbeat

## Debug logs

Managed BE stdout/stderr is captured under:

```text
Nodes/Supervisor/logs/<node_endpoint>/<unique_id>.log
```

Supervisor bootstrap (canvas / Start BE) writes to:

```text
Nodes/Supervisor/logs/supervisor/supervisor.log
```

In the operator panel, select a Running/exited row to tail that instance’s log.
Stop clears the Redis hash; log files remain on disk.
