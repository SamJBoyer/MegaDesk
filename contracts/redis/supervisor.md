# Supervisor Redis packages

Supervisor uses Redis for:

1. **Pub/Sub** request/ack control plane (DB 0)
2. **Commander alive** heartbeat key (DB 0)

Node backends are discovered from installed `MegaDesk.nodes` entry points via
`get_exec_spec("BE")` → `BeSpec` (argv + optional cwd). There are no YAML
manifests and no parameter-hash upload on launch.

This family is independent of the Plant pipeline streams, but shares the same
localhost Redis server.

---

## Databases

| DB | Role |
|----|------|
| `0` | Pub/Sub channels, `GBD:COMMANDER:ALIVE` |

Launch contract for BE nodes:

```text
subprocess.Popen(BeSpec.argv, cwd=BeSpec.cwd)
```

Managed by nickname = `BeSpec.name`.

---

## Pub/Sub channels

Caller identity scopes every request/ack pair.

### Request patterns (commander subscribes)

| Pattern / channel | Message body | Ack on success | Ack on failure |
|-------------------|--------------|----------------|----------------|
| `launch_node:<caller_identity>` | Node name (`BeSpec.name`) | `SUCCESS` | `FAILED` |
| `stop_node:<caller_identity>` | Node name | `SUCCESS` | `FAILED` |
| `KILLALL` | (any / ignored) | none — kills all managed nodes | — |

### Ack channel (caller subscribes)

| Channel | Payload |
|---------|---------|
| `acknowledgements:<caller_identity>` | `SUCCESS` or `FAILED` |

### Caller sequence

1. Subscribe to `acknowledgements:<caller_identity>`
2. Publish to `<action>:<caller_identity>` with body
3. Wait for ack on the acknowledgements channel

Typical MegaDesk canvas path: after dropping an FE node that also exposes a BE,
publish `launch_node:<identity>` with the node name.

**Supervisor bootstrap:** dropping the `supervisor` FE does **not** use
`launch_node` (the commander is not up yet). MegaDesk / the FE call
`megadesk.ensure_supervisor_running()`, which spawns the Supervisor `BeSpec`
(`python -m commander`) directly and waits for `GBD:COMMANDER:ALIVE`.

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

See `Supervisor/commander/redis_provision.py`.
