# Supervisor / GBD Redis packages

Supervisor (GeniusBrainDisease commander) uses Redis for:

1. **Pub/Sub** request/ack control plane (DB 0)
2. **Node parameter hashes** read by launched processes (DB 1)
3. **Commander alive** heartbeat key (DB 0)

This family is independent of the Plant pipeline streams, but shares the same localhost Redis server.

---

## Databases

| DB | Role |
|----|------|
| `0` | Pub/Sub channels, `GBD:COMMANDER:ALIVE` |
| `1` | `PARAMETERS_<nickname>` hashes |

Launch contract for nodes (CLAM `PyNode`):

```text
python <target> -n <nickname> -i localhost -p 6379
```

cwd = resolved node `directory`. Nickname must match the manifest node id so `PARAMETERS_<nickname>` resolves on DB 1.

---

## Pub/Sub channels

Caller identity scopes every request/ack pair.

### Request patterns (commander subscribes)

| Pattern / channel | Message body | Ack on success | Ack on failure |
|-------------------|--------------|----------------|----------------|
| `register_manifest:<caller_identity>` | Absolute/relative path to manifest YAML | `SUCCESS <GUID>` | `FAILED` |
| `validate_manifest:<caller_identity>` | Manifest path | `SUCCESS` | `FAILED` |
| `execute_manifest:<caller_identity>` | GUID from register | `SUCCESS` | `FAILED` |
| `KILLALL` | (any / ignored) | none — kills all managed nodes | — |

### Ack channel (caller subscribes)

| Channel | Payload |
|---------|---------|
| `acknowledgements:<caller_identity>` | `SUCCESS …` or `FAILED` as above |

### Caller sequence

1. Subscribe to `acknowledgements:<caller_identity>`
2. Publish to `<action>:<caller_identity>` with body
3. Wait for ack on the acknowledgements channel

Registered manifests are **session-only** (GUID stash in commander memory; not persisted in Redis across commander restart).

---

## PARAMETERS\_\<nickname\>

| Property | Value |
|----------|-------|
| Type | Hash |
| Database | **1** |
| Key | `PARAMETERS_<nickname>` |
| Writer | Commander on execute (delete-then-`HSET`) |
| Reader | Launched node at init |

### Fields

Whatever the manifest declares under `nodes.<nickname>.parameters`. All values are stored as strings.

Reference fixture (`ol.yaml` → `TrialRunnerOL`):

| Hash field | Example value |
|------------|---------------|
| `experiment_path` | `AS_OL.txt` |
| `target_dir` | `assets/elbow/track` |
| `start` | `assets/elbow/halfway.json` |
| `threshold` | `1` |
| `speed` | `5` |
| `frame_rate` | `60` |

Key for that fixture: `PARAMETERS_TrialRunnerOL` on DB 1.

---

## GBD:COMMANDER:ALIVE

| Property | Value |
|----------|-------|
| Type | String |
| Database | **0** |
| Key | `GBD:COMMANDER:ALIVE` |
| Value | `"1"` |
| TTL | 5 seconds (refreshed by commander heartbeat) |

Presence of the key means the commander process is reachable. Cleared on commander shutdown.

---

## Connection / provision

| Setting | Convention |
|---------|------------|
| Host / port | `localhost:6379` |
| Prefer | Attach to existing Redis |
| Else | Docker container `gbd-redis` (`redis:7`) + optional `gbd-redis-insight` on port `5540` |

See `Supervisor/commander/redis_provision.py` and `Supervisor/PRD.md` §5.2–5.3 for the requirement IDs this contract implements.
