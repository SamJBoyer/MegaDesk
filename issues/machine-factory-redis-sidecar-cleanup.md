# MachineFactory: stop Redis sidecars when agent sandboxes finish

**Labels:** `agent-ready`

## Problem

Every `WORKORDER` starts **two** Docker containers:

1. **Agent sandbox** — `mf-{repo}-ticket-{ticket}` (`machine-factory-agent:latest`, `--rm`)
2. **Redis sidecar** — `mf-redis-{guid}` (`redis:7-alpine`, `--rm`, label `megadesk.redis_for={guid}`)

The agent sandbox exits when `AgentHandler` finishes and is removed by `--rm`. The Redis sidecar **keeps running indefinitely** after successful runs because nothing stops it.

Reproduce:

1. Publish one `WORKORDER` and wait for the agent to finish.
2. Run `docker ps --filter name=mf-`.
3. Observe: agent container is gone; one or more `mf-redis-*` containers remain `Up`.

The MachineFactory FE Docker panel (`machine_factory_frontend/app.py`, filter `name=mf-`) will show these orphaned sidecars accumulating across runs.

## Root cause

Sidecar cleanup (`stop_redis_sidecar` / `DockerSandboxFactory.release`) is only invoked on:

- launch failure (`manager.py` → `runtime.release` in the except path),
- explicit cancel (`runtime.cancel`),
- reaper path (`poll_runs` → `_reap` → `release`) when the sandbox vanished **without** publishing `FINISHED`.

On the **happy path**, `teardown_node` calls `publish_finished`, which **deletes** `AGENTHANDLER:<guid>`, then the agent container exits. `poll_runs` only walks `AGENTHANDLER:*` hashes (`live_runs()`), so it never sees finished runs and never calls `release`. The sidecar has `--rm` but only auto-removes **after stop** — and nothing stops it.

Relevant code:

- Sidecar start: `MachineFactoryManager/pool.py` — `start_redis_sidecar`, `start_ticket_sandbox`
- Sidecar stop: `pool.py` — `stop_redis_sidecar`; wired via `runtime.py` — `release`
- Happy-path teardown: `AgentHandler/graph/nodes.py` — `teardown_node` → `publish_finished` (deletes hash)
- Reaper (abnormal only): `MachineFactoryManager/manager.py` — `poll_runs`, `_reap`

## Fix (recommended)

Add sidecar cleanup that does **not** depend on `AGENTHANDLER` hashes still existing.

### Option A (preferred): `poll_sidecars` in the manager loop

Add a method on `MachineFactoryManager` (e.g. `poll_sidecars`) called from `poll_once` alongside `poll_runs`:

1. List running containers with label `megadesk.redis_for` (reuse `REDIS_RUN_LABEL` from `pool.py`; consider a small helper like `list_redis_sidecars()`).
2. For each sidecar's `guid`, check whether the agent sandbox for that run key is still running (`container_for_run(guid)` + `container_is_running`).
3. If the agent sandbox is **missing or not running**, call `runtime.release(guid)` (which calls `stop_redis_sidecar`).

This covers:

- successful completion (hash already deleted),
- crash / kill without `FINISHED`,
- manager restart (labels persist on containers).

Run it on the same interval as `poll_runs` (or every `poll_once` tick — sidecar list is cheap).

### Option B (supplement, optional)

When `poll_runs` sees a live hash and `runtime.poll(guid)` returns `finished`, call `runtime.release(guid)` **before** orphan-grace reaping. This alone does not fix the happy path (hash deleted first) but tightens the abnormal path.

Do **not** stop the sidecar from inside `AgentHandler` — the sandbox cannot reliably `docker stop` its own sidecar and should stay ignorant of host Docker.

## Files to touch

| Path | Change |
|------|--------|
| `Nodes/Factory/MachineFactory/MachineFactoryManager/pool.py` | Helper to list sidecar containers by label (if needed) |
| `Nodes/Factory/MachineFactory/MachineFactoryManager/manager.py` | `poll_sidecars`, wire into `poll_once` |
| `Nodes/Factory/MachineFactory/MachineFactoryManager/runtime.py` | No change required if `release` is reused |
| `tests/test_machinefactory_flow.py` | Assert sidecar release on simulated successful completion |
| `Nodes/Factory/MachineFactory/README.md` | One line: sidecars are reaped when the agent sandbox is gone |

Keep the diff minimal. Do not change wire formats, Redis keys, or FE layout.

## Tests

Extend `tests/test_machinefactory_flow.py` (uses `FakeMachineFactory`; no real Docker):

1. **Happy path releases sidecar:** After simulating a sandbox that publishes `FINISHED`, deletes its hash, and stops (`fake_machine_factory.stop`), running the new cleanup (`poll_sidecars` or equivalent) must call `fake_machine_factory.release` for that `run_key`.
2. **Live sandbox leaves sidecar alone:** While `fake_machine_factory.poll` reports `running`, cleanup must **not** release.
3. **Existing reaper tests still pass** — do not break `test_a_sandbox_that_vanished_is_reaped_and_reported` or `test_a_healthy_sandbox_reporting_for_itself_is_not_reaped_twice`.

If you add a `pool.py` list helper, add a small unit test in `tests/test_sandbox_redis_sidecar.py` (mock `_docker` like other pool tests).

Run from repo root (MEGADESK conda env):

```bash
conda activate MEGADESK
python scripts/down_nodes.py
pytest tests/test_machinefactory_flow.py tests/test_sandbox_redis_sidecar.py -q
```

## Acceptance criteria

- [ ] After one successful MachineFactory run, `docker ps --filter name=mf-redis` shows **no** containers for that run's guid within one manager poll cycle (~10s).
- [ ] Cancelled and failed launches still release sidecars (existing behavior preserved).
- [ ] Manager restart: orphaned sidecars from prior runs are cleaned up once the agent container is no longer running.
- [ ] New/updated tests pass; no unrelated refactors.

## Constraints

- Use the **MEGADESK** conda environment.
- Before testing locally: `python scripts/down_nodes.py`.
- After node package changes: `python scripts/refresh_nodes.py`.
- Follow existing naming and logging patterns in `MachineFactoryManager`.
- Do not add verbose GUI text or new FE features.

## Out of scope

- Supervisor-provisioned `megadesk-redis` / `megadesk-redis-insight` containers (different subsystem; intentionally persistent).
- Changing the per-run sidecar architecture (isolated Redis for sandbox agents is intentional).
