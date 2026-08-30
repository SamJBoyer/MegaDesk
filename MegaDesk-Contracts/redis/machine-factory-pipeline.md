# MachineFactory pipeline packages

Defined once in `megadesk_contracts/wire/machine.py`, on the status vocabulary in
`wire/factory.py`. Its cloud counterpart is [voice-chain.md](voice-chain.md#cloudorder),
and the two are the same shape on purpose — see
[`Nodes/Factory/README.md`](../../Nodes/Factory/README.md).

MachineFactory clones the named repo into a Docker sandbox, gives the agent a
Redis **sidecar** as its `REDIS_URL`, and keeps factory IPC on
`MEGADESK_FACTORY_REDIS_URL` (host pair). On done it hands back a PR URL as
factory outcome on `FINISHED:<REPO>`. PRManager does not read that stream — it
lists open PRs whose merge-check `mergeable` check succeeded.

Flow:

```
WorkDispatcher
        │  PUBLISH
        ▼
   WORKORDER (channel)
        │  SUBSCRIBE
        ▼
   MachineFactoryManager
        │  XADD (reference only; leftover entries do not start work)
        ▼
   WORKORDER (stream)
        │  HSET + Docker sandbox (+ Redis sidecar)
        ▼
   AGENTHANDLER:<GUID> (hash)
        │  HGETALL + XRANGE WORKORDER by ticket_id
        ▼
   AgentHandler
        │  clone → work → PR
        │  HSET GRAPHRUN:<GUID> / XADD GRAPHEVENT
        │  XADD + DEL hashes
        ▼
   FINISHED:<REPO> (stream, factory outcome)

GitHub `mergeable` check (success)
        │  gh pr list
        ▼
   PRManager (show / open PR)
```

---

## WORKORDER (channel)

| Property | Value |
|----------|-------|
| Type | Pub/sub channel |
| Name | `WORKORDER` |
| Subscriber | MachineFactoryManager |
| Producers | WorkDispatcher, AutoIntegrate, manual `PUBLISH` |

A `PUBLISH` is the only execution signal. If the factory is not subscribed, the
order is dropped — leftover tickets cannot re-run after a restart.

## WORKORDER (stream)

| Property | Value |
|----------|-------|
| Type | Stream (reference store) |
| Key | `WORKORDER` |
| Writer | MachineFactoryManager, after it receives the pub/sub signal |
| Readers | AgentHandler (`XRANGE` by ticket_id), factory FEs |

### Fields

| Field | Required | Notes |
|-------|----------|-------|
| `repo` | yes | Repo name (e.g. `Helmsman`) |
| `URL` | yes | Remote git URL for clone; casing is `URL` |
| `ticket_name` | yes | Ticket / branch name |
| `instructions` | yes | Agent prompt body |
| `model` | no | Model id; default `"auto"` |
| `auto_pr` | no | `"true"` / `"false"`; default `"true"` |
| `pictures` | no | JSON list of image URLs for agent context; default `[]` |
| `issue` | no | GitHub issue number when the order came from a labeled ticket |
| `graph` | no | AgentHandler work graph: `work` (default) or `massive` |

### Stream id as ticket id

The Redis stream entry id returned by `XADD` (e.g. `1712345678901-0`) is the **ticket_id** referenced by `AGENTHANDLER:<GUID>.ticket_id` and later echoed on `FINISHED:<REPO>`.

### Example

```text
PUBLISH WORKORDER {"repo":"Helmsman","URL":"https://github.com/example/Helmsman.git","ticket_name":"1","instructions":"Create harness-smoke.txt with the text ok","model":"auto","auto_pr":"true","ref":"","issue":"","graph":"work"}
```

The factory then `XADD`s the same fields onto the `WORKORDER` stream. That stream
id is the **ticket_id**. A leftover `XADD` with no matching `PUBLISH` does not
start a sandbox.

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

Ticket payload (`ticket_name`, `instructions`, `model`, `URL`, `auto_pr`, `graph`) is **not** stored on this hash. AgentHandler loads those from `WORKORDER` via `ticket_id`, so they cannot drift from what was ordered.

---

## FINISHED:\<REPO\>

| Property | Value |
|----------|-------|
| Type | Stream (**not** a list) |
| Key | `FINISHED:<REPO>` where `<REPO>` is the repo name (same as `WORKORDER.repo`) |
| Consumer group | `merge_manager` (unused by any FE; kept as the historical group name) |
| Primary consumer | none — PRManager reads GitHub `mergeable` checks instead |
| Producer | AgentHandler; MachineFactoryManager for launches that failed and for sandboxes reaped after dying without reporting |

### Fields

| Field | Required | Notes |
|-------|----------|-------|
| `ticket_name` | yes | Ticket name |
| `ticket_id` | yes | Originating `WORKORDER` stream id |
| `status` | yes | One of `RUN_STATUSES` |
| `pr_url` | no | Pull-request URL; may be `""` on error paths |

### Example

```text
XRANGE FINISHED:Helmsman - +
```

---

## Execution vs reference

`WORKORDER` pub/sub starts work. The `WORKORDER` stream is a reference store
written by the factory after it receives the signal. Stream consumer groups are
not the execution path.

`FINISHED:<REPO>` is factory outcome telemetry. No FE currently joins `merge_manager`.

---

## Related env / side channels

Not Redis packages, but part of the same contract:

| Env | Meaning |
|-----|---------|
| `GUID` | Sandbox id → hash key `AGENTHANDLER:<GUID>` |
| `TICKET_ID` | Fallback ticket id if hash missing |
| `REPO_URL` | Clone URL (mirrors `WORKORDER.URL`) |
| `REPO_NAME` | Repo segment for `FINISHED:<REPO>` |
| `REDIS_URL` | Agent MegaDesk Redis **sidecar** inside the sandbox |
| `MEGADESK_FACTORY_REDIS_URL` | Factory bus on the host pair (WORKORDER / AGENTHANDLER / FINISHED) |
