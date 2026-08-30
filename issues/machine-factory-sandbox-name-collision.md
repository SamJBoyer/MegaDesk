# MachineFactory: unique sandbox names so a second WORKORDER cannot kill a live run

**Labels:** `agent-ready`

## Problem

Agent sandboxes are named from **repo + ticket only**:

```text
mf-{repo}-ticket-{ticket}
```

A second `WORKORDER` for the same repo and ticket name reuses that Docker name. `start_ticket_sandbox` then **force-removes** the live container ("Removing existing container … before restart") and starts the new run in its place.

The first run is cut mid-graph. It never reaches teardown, so it never publishes `FINISHED`. The manager later reaps it as `sandbox stopped without publishing FINISHED`.

Seen on 2026-08-30 (`Logs/2026-08-30T18-29-36Z`):

1. 14:30:08 — ticket `Rewrite all gui code in NiceGui`, graph `massive`, model `grok-4.6`, guid `1e912855-…`. Orchestrator was surveying the clone.
2. 14:31:49 — same ticket + graph, model `claude-opus-5`, guid `41082683-…`. Pool logged `Removing existing container mf-megadesk-ticket-rewrite-all-gui-code-in-nicegui before restart`.
3. First sandbox log-follow ended. Sidecar for `1e912855` was released because the agent container was gone. At 14:32:24 the reaper published `FINISHED:MegaDesk` for the killed run.

Redis sidecars already include the guid (`mf-redis-{guid}`) and did not collide. Only the agent container name is shared.

Reproduce:

1. Publish a `WORKORDER` (any graph).
2. While that sandbox is still running, publish a second `WORKORDER` with the same `repo` + `ticket_name` (different `model` is enough).
3. Observe: first container is `docker rm -f`'d; first `agent-{guid}.md` stops mid-step; later `FINISHED` for the first guid says the sandbox vanished.

## Root cause

`container_name(repo, ticket)` in `MachineFactoryManager/pool.py` does not take the run guid. `start_ticket_sandbox` then:

```python
name = container_name(repo, ticket)
if inspect(name) succeeds:
    log.info("Removing existing container %s before restart", name)
    remove_container(name)  # docker rm -f — kills whoever holds that name
```

That remove was written as "restart the same named box", not "two live runs". WorkDispatcher can legally publish two orders for one ticket (medium then high, retry, fat-finger). Each order already has its own `AGENTHANDLER:<guid>` and Redis sidecar. The agent container is the only object that cannot coexist.

`poll` / `cancel` already find sandboxes by label `megadesk.run_key=<guid>` (`container_for_run`), not by ticket-derived name. Unique names do not break manager restart.

Each sandbox clones into its own `/workspace`. There is no shared Floor worktree, so two live clones of the same ticket are safe on disk.

## Fix (recommended)

Give the agent container the same uniqueness the sidecar already has: **put the run guid in the name**, and **never `rm -f` a container that belongs to another run**.

### 1. Name includes guid

Change `container_name` to take `guid` (required). Sanitize like `redis_sidecar_name` (lowercase, Docker-safe charset). Keep repo + ticket in the name so `docker ps` stays readable:

```text
mf-{repo}-ticket-{ticket}-{guid-token}
```

`guid-token` = sanitized guid, truncated the same way as sidecars (48 chars of token). If the full name is awkwardly long, truncate the **ticket** slug, not the guid — uniqueness lives on the guid.

Call site: `start_ticket_sandbox` already has `guid`. Pass it through.

### 2. Only remove leftover for *this* guid

After the name includes the guid, `inspect` + `remove` is only for a crashed leftover of **this** run (same name). Do not search for, or remove, `mf-{repo}-ticket-{ticket}` without a guid, and do not remove any other run's container.

Drop or rewrite the log line `Removing existing container %s before restart` so it cannot be read as "replace the live ticket". If you keep a leftover-cleanup, log that it is this guid's stale name.

### 3. Fake + docs

`FakeMachineFactory.launch` currently builds `mf-{repo}-ticket-{ticket}`. Include `run_key` in that string so tests that store `container` stay honest. No test today asserts the exact ticket-only name.

Update the one-liner in `Nodes/Factory/MachineFactory/README.md` if it still describes ticket-only names. The FE lists `docker ps --filter name=mf-`; unique suffixes still match. No FE layout change.

## Files to touch

| Path | Change |
|------|--------|
| `Nodes/Factory/MachineFactory/MachineFactoryManager/pool.py` | `container_name(repo, ticket, guid)`; stop killing other runs in `start_ticket_sandbox` |
| `MegaDesk-Contracts/megadesk_contracts/testing/fakes.py` | `FakeMachineFactory` container string includes `run_key` |
| `tests/test_sandbox_redis_sidecar.py` (or a sibling `test_sandbox_container_name.py`) | Pure name-helper tests (no Docker) |
| `tests/test_machinefactory_flow.py` | Two same-ticket launches keep two names; second start does not `rm` the first |
| `Nodes/Factory/MachineFactory/README.md` | One line: sandbox name includes the run guid |

Keep the diff minimal. Do not change wire formats, Redis keys, work-graph nodes, or FE widgets. Sidecar naming stays as it is.

## Tests

No real Docker required. Mock `_docker` the way `test_sandbox_redis_sidecar.py` and `test_the_sandbox_environment_carries_the_ref_and_defaults_to_dev` already do.

1. **Name includes guid and is Docker-safe.** `container_name("MegaDesk", "Rewrite all gui!", "ABC 123")` contains a sanitized guid token, has no spaces / `!`, is lowercase, starts with `CONTAINER_NAME_PREFIX`. Two guids with the same repo+ticket produce **different** names.
2. **Same ticket, two starts, no cross-kill.** Patch `_docker`, `start_redis_sidecar`, `ensure_network`, `_follow_container_logs`. Call `start_ticket_sandbox` twice with the same repo+ticket and different guids. Assert:
   - `--name` on the two `docker run` arg lists differ
   - `remove_container` / `docker rm` is **not** invoked with the first container's name
3. **Same guid leftover may be removed.** If `inspect` says *this* guid's name already exists, it is fine to `rm` that one name only (crash leftover). Do not `rm` any other name.
4. **Existing tests still pass** — especially `test_the_sandbox_environment_carries_the_ref_and_defaults_to_dev` (it already calls `start_ticket_sandbox` with `guid="guid-01"`).

`FakeMachineFactory` does not need a new manager-loop test unless you change `launch` in a way that breaks `run_id`. If you update the fake container string, grep that nothing asserts the old ticket-only form.

Run from repo root (MEGADESK conda env):

```bash
conda activate MEGADESK
python scripts/down_nodes.py
pytest tests/test_machinefactory_flow.py tests/test_sandbox_redis_sidecar.py -q
```

Add the new name-helper file to that line if you create one.

## Acceptance criteria

- [ ] Two live `WORKORDER`s with the same `repo` + `ticket_name` produce two running agent containers and two Redis sidecars.
- [ ] The second launch does **not** `docker rm -f` the first agent container.
- [ ] `poll` / `cancel` / `poll_runs` still find a sandbox by `megadesk.run_key` after a manager restart.
- [ ] MachineFactory FE Docker list still shows both (`name=mf-` filter).
- [ ] New/updated tests pass; no unrelated refactors.

## Constraints

- Use the **MEGADESK** conda environment.
- Before testing locally: `python scripts/down_nodes.py`.
- After node package changes: `python scripts/refresh_nodes.py`.
- Follow existing naming and logging patterns in `MachineFactoryManager` (sidecar helpers are the template).
- Do not add verbose GUI text or new FE features.

## Out of scope

- Cursor / Opus usage-limit failures (agent status error → teardown). That is provider quota, not this collision.
- Changing ticket branch names (`ticket/{ticket_name}`). Two finishing runs of the same ticket may still contend on the same git branch / PR; that is a later problem. This issue is only "do not kill the live sandbox".
- Supervisor-provisioned `megadesk-redis` containers.
- Work-graph / orchestrator / kanban behavior.
