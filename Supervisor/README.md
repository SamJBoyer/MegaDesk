# GBD Commander

Windows supervisor backend for GBD. Provisions Redis, accepts manifest register/validate/execute requests over Pub/Sub, and launches node processes.

## Setup

```bat
pip install -r requirements.txt
rem or: pip install -e .
```

## Run

```bat
start_commander.bat
rem or: python -m commander
```

## Smoke test

With Redis available and a valid `ol.yaml` (plus any `~NODES/` targets on disk):

```bat
python -m commander.smoke_test
```

## Layout

- `commander/` — Python package (`client`, `engine`, `pubsub_server`, …)
- `ol.yaml` — MVP manifest fixture (paths resolve relative to this repo root)
- `hDocs/`, `HELMSMAN.md`, `PRD.md` — product / Helmsman docs
