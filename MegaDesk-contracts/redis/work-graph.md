# Work graph packages

Defined once in `megadesk_contracts/wire/graph.py`, on the status vocabulary in
`wire/factory.py`. Written from inside the AgentHandler sandbox onto the factory
bus (`MEGADESK_FACTORY_REDIS_URL`), because the sandbox's own `REDIS_URL` is a
leased lane the canvas never sees.

These keys sit beside `AGENTHANDLER:<guid>`, they do not replace it. The factory
still reaps against the handshake hash; GraphScope watches this family for
where inside the run the sandbox is.

```
AGENTHANDLER:<GUID>          (hash, handshake — factory + sandbox)
        │
        ▼
   AgentHandler work graph
        │  HSET
        ▼
   GRAPHRUN:<GUID>            (hash, live node progress)
        │  XADD
        ▼
   GRAPHEVENT                 (stream, timeline)
        │
        ▼
   teardown: XADD FINISHED:<REPO>, DEL both hashes
```

---

## GRAPHRUN:\<GUID\>

| Property | Value |
|----------|-------|
| Type | Hash |
| Key | `GRAPHRUN:<guid>` |
| Live DB | 0 (factory bus) |
| Writer | AgentHandler graph reporter |
| Readers | GraphScope (SCAN), anything watching a live run |

Deleted at teardown with the `AGENTHANDLER` hash, so a missing key still means
"no run".

### Fields

| Field | Required | Notes |
|-------|----------|-------|
| `guid` | yes | Same guid as `AGENTHANDLER:<guid>` |
| `graph` | yes | Spec name (`work`) |
| `spec` | yes | JSON `GraphSpec` (nodes, edges, kinds) — the shape that actually ran |
| `nodes` | yes | JSON map of node name → `{status, started, ended, detail}` |
| `current` | no | Node running right now; empty between nodes and after finish |
| `status` | yes | Run status from `wire.factory` |
| `ticket_id` | no | Originating `WORKORDER` stream id |
| `ticket_name` | no | |
| `repo` | no | |
| `started` | no | ISO-8601 |
| `updated` | no | ISO-8601; writers stamp this |
| `error` | no | |

Node `status` reuses the same vocabulary as a run (`queued`, `running`,
`finished`, `error`, `cancelled`, …). A node that was never reached stays
`queued`.

---

## GRAPHEVENT

| Property | Value |
|----------|-------|
| Type | Stream |
| Key | `GRAPHEVENT` |
| Live DB | 0 (factory bus) |
| Cap | `MAXLEN ~ 4096` |
| Writer | AgentHandler graph reporter |
| Readers | GraphScope (`XREVRANGE`, filter by `guid`) |

The stream outlives the hash on purpose. After teardown the hash is gone; the
events are what happened.

No consumer group. This is a timeline, not a queue.

### Fields

| Field | Required | Notes |
|-------|----------|-------|
| `guid` | yes | Run guid |
| `graph` | yes | Spec name |
| `node` | yes | Graph node that changed |
| `status` | yes | Shared factory status |
| `detail` | no | Short note |
| `ts` | yes | ISO-8601; writers stamp this if omitted |

---

## Topology

The default spec is `WORK_GRAPH`:

```
startup_node → pathfinder_node → workhorse_node → git_node → teardown_node
```

Each non-terminal node also has a conditional edge to `teardown_node` when
`state["error"]` is set, so `FINISHED:<repo>` is always published and both
hashes are always deleted.

The hash carries its own `spec` so a visualizer draws what ran, not what it was
compiled against.
