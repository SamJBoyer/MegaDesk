# Supervisor

MegaDesk process lifecycle manager. Provisions Redis, discovers BE nodes from
installed `MegaDesk.nodes` entry points, and launches them as managed subprocesses
via Redis Pub/Sub (`launch_node` / `stop_node` / `KILLALL`).

## Setup

```bat
pip install -e ../megadesk
pip install -e .
```

## Run

```bat
start_commander.bat
rem or: python -m commander
```

## Smoke test

With Redis available and Plant installed (`pip install -e ../Plant`):

```bat
python -m commander.smoke_test
```

## Layout

- `commander/` — Python package (`client`, `engine`, `pubsub_server`, …)
- `hDocs/`, `HELMSMAN.md`, `PRD.md` — product / Helmsman docs (PRD still describes the old manifest model)
