# MachineFactory pipeline packages

Defined once in `megadesk_contracts/wire/machine.py`, on the status vocabulary in
`wire/factory.py`. Its cloud counterpart is [voice-chain.md](voice-chain.md#cloudorder),
and the two are the same shape on purpose — see
[`Nodes/Factory/README.md`](../../Nodes/Factory/README.md).

Flow:

```
TicketDispatcher / MergeManager
        │  XADD
        ▼
   WORKORDER (stream)
        │  XREADGROUP group=machine_factory
        ▼
   MachineFactoryManager
        │  HSET + Docker sandbox
        ▼
   AGENTHANDLER:<GUID> (hash)
        │  HGETALL + XRANGE WORKORDER by ticket_id
        ▼
   AgentHandler
        │  work graph (startup → pathfinder → workhorse → git → teardown)
        │  HSET GRAPHRUN:<GUID> / XADD GRAPHEVENT
        │  XADD + DEL hashes
        ▼
   FINISHED:<REPO> (stream)
        │  XREADGROUP group=merge_manager
        ▼
   MergeManager
```

---

## WORKORDER

| Property | Value |
|----------|-------|
| Type | Stream |
| Key | `WORKORDER` |
| Consumer group | `machine_factory` |
| Primary consumer | MachineFactoryManager |
| Producers | TicketDispatcher (`new_wt=true`), MergeManager conflict path (`new_wt=false`), manual `XADD` |

### Fields

| Field | Required | Notes |
|-------|----------|-------|
| `repo` | yes | Floor repo name (e.g. `Helmsman`) |
| `URL` | when creating Floor / new worktree | Remote git URL; casing is `URL` |
| `new_wt` | yes | `"true"` create ticket worktree from agents; `"false"` mount existing `wt` |
| `wt` | when `new_wt=false` | Absolute host path to existing worktree; `""` when `new_wt=true` |
| `ticket_name` | yes | Ticket / worktree name |
| `instructions` | yes | Agent prompt body |
| `model` | no | Model id; default `"auto"` |

### Stream id as ticket id

The Redis stream entry id returned by `XADD` (e.g. `1712345678901-0`) is the **ticket_id** referenced by `AGENTHANDLER:<GUID>.ticket_id` and later echoed on `FINISHED:<REPO>`.

### Example

```text
XADD WORKORDER * repo Helmsman URL https://github.com/example/Helmsman.git new_wt true wt "" ticket_name 1 instructions "Create harness-smoke.txt with the text ok" model auto
```

### Accepted parse aliases (readers only)

`parse_workorder` tolerates legacy/alternate keys when reading:

- `repo` / `REPO`
- `URL` / `url`
- `ticket_name` / `ticket` / `name`
- `instructions` / `instruction` / `prompt` / `text`

Writers should use the canonical field names in the table above.

---

## AGENTHANDLER:\<GUID\>

| Property | Value |
|----------|-------|
| Type | Hash |
| Key | `AGENTHANDLER:<GUID>` |
| Writer | MachineFactoryManager (create, reap), AgentHandler (status updates) |
| Reader | AgentHandler, MachineFactory FE |
| Lifetime | Deleted after publish to `FINISHED:<REPO>` (or on early finish/error paths that still publish) |

### Fields

| Field | Required | Notes |
|-------|----------|-------|
| `ticket_id` | yes | Stream id of the originating `WORKORDER` entry |
| `status` | no | `queued`, `running`, `finished`, `error`, `cancelled`, `startup_error` — the shared factory vocabulary, validated on write |
| `error` | no | Error message when status is error-like; otherwise `""` |

This hash is also the handshake: MachineFactoryManager writes it **before** the container starts, because the sandbox reads its own GUID out of the environment to find its work here. A missing hash therefore means "no run", which is what makes the FE's live list truthful without reconciling it.

Ticket payload (`ticket_name`, `instructions`, `model`, paths) is **not** stored on this hash. AgentHandler loads those from `WORKORDER` via `ticket_id`, so they cannot drift from what was ordered. Host absolute paths for the finished package are passed into the container as env (`HOST_WT`, `HOST_AGENT_DIR`).

---

## FINISHED:\<REPO\>

| Property | Value |
|----------|-------|
| Type | Stream (**not** a list) |
| Key | `FINISHED:<REPO>` where `<REPO>` is the Floor repo name (same as `WORKORDER.repo`) |
| Consumer group | `merge_manager` |
| Primary consumer | MergeManager |
| Producer | AgentHandler; MachineFactoryManager for launches that failed and for sandboxes reaped after dying without reporting |

### Fields

| Field | Required | Notes |
|-------|----------|-------|
| `ticket_name` | yes | Ticket name |
| `ticket_id` | yes | Originating `WORKORDER` stream id |
| `wt` | yes | Absolute path to the ticket worktree |
| `agent_dir` | yes | Absolute path to the agents worktree |

### Example

```text
XRANGE FINISHED:Helmsman - +
```

### Accepted parse aliases (readers only)

- `ticket_name` / `ticket`
- `wt` / `workpath`

---

## Consumer groups & ack rules

| Stream | Group | Ack when |
|--------|-------|----------|
| `WORKORDER` | `machine_factory` | After MachineFactoryManager finishes handling the entry (success or handled failure) |
| `FINISHED:<REPO>` | `merge_manager` | After MergeManager processes / dismisses the item (may also `XDEL`) |

Groups are created with `XGROUP CREATE … MKSTREAM` if missing (`BUSYGROUP` is ignored).

---

## Related env / side channels

Not Redis packages, but part of the same contract:

| Env | Meaning |
|-----|---------|
| `GUID` | Sandbox id → hash key `AGENTHANDLER:<GUID>` |
| `TICKET_ID` | Fallback ticket id if hash missing |
| `HOST_WT` / `HOST_AGENT_DIR` | Absolute host paths written into `FINISHED` |
| `REPO_NAME` | Repo segment for `FINISHED:<REPO>` |
