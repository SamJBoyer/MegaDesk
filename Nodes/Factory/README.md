# Factory

A Factory deploys agents. It reads orders, builds somewhere for an agent to work,
starts a harness that carries instructions in and results out, and follows the run
until it is over.

Two of them live here, and they are separate nodes because their infrastructure is
genuinely different, not because their jobs are:

| Node | Runs agents | Harness | Hands back |
|------|-------------|---------|------------|
| [MachineFactory](MachineFactory/README.md) | Docker sandboxes on this machine (repo clone + Redis sidecar) | `AgentHandler/`, ours end to end | a pull request |
| [CloudFactory](CloudFactory/README.md) | Cursor-hosted VMs | the cloud agent SDK, whatever it gives us | a pull request |

## Why they look alike on purpose

The goal is a graph where a node is "an agent doing a piece of work" and the graph
controller decides where that work runs. That only holds if the two factories are
interchangeable at the seam, so a scheduling decision never turns into a
capability decision.

Both are shaped the same way: an order stream, one hash per live run, a finished
stream, and a `manager.py` order loop over a `runtime.py` that actually starts
things.

```text
order stream  →  manager.py  →  runtime.py  →  agent somewhere
                     ↓              ↑
              hash per live run ─────┘
                     ↓
              finished stream
```

Both runtimes implement one protocol, `megadesk_contracts.factory.AgentFactory`:

```python
launch(order: Mapping) -> RunHandle    # run_key to track it by, run_id to show
poll(run_key: str)     -> RunStatus    # status, result, detail
cancel(run_key: str)   -> None
```

`launch` takes a mapping rather than named arguments so one caller can hand the
same parsed order to either factory; each reads the keys it understands. The
shared keys are `title`/`ticket_name`, `instructions` and `model`. Beyond those,
a machine order names a `repo` plus clone `URL` and `auto_pr`, while a cloud
order names a `repo_url`, a `ref` and `auto_pr`. Both hand back a PR URL.

Status words come from `megadesk_contracts.wire.factory` and mean the same thing
on both sides: `queued`, `running`, `finished`, `error`, `cancelled`,
`startup_error`. `normalize_status` maps provider vocabulary onto them, and an
unknown word reads as `running` — guessing `finished` would close a run that is
still writing to a branch.

Both keep the two failure modes apart, because they need different fixes.
`AgentStartupError` means no agent ever existed (auth, config, rate limit) and is
the only one retried, on the provider's own advice. A run that started and failed
reports `error`, and the transcript is what to look at.

The FEs follow the same split. Both show **queued work orders** and **live
agents**, plus a corner lamp that turns red if an error has been thrown.
MachineFactory also shows active sandboxes. Node logs are in the Supervisor Logs
tab, not on the factory.

Both take their orders from WorkDispatcher (and VoiceDeck can also publish
`CLOUDORDER`). Each ticket row picks machine or cloud the same way it picks a
model, so one click writes either `WORKORDER` or `CLOUDORDER`. Neither factory
has its own GitHub URL or issue-text input.

## Where they honestly differ

- **Who mints the run key.** MachineFactory mints a guid and writes
  `AGENTHANDLER:<guid>` *before* starting the container, because the container
  reads that hash to find its work. In the cloud, Cursor mints `bc-…` and the
  registry entry can only be written afterwards. That is why `launch` accepts a
  `run_key` on the order: a factory whose handshake needs one uses it, and the
  other ignores it.
- **Who notices the end.** A cloud run is a managed service you can ask about. A
  container is not: a healthy sandbox reports its own outcome from inside, where
  the exit code is, so `poll_runs` on the machine side is a reaper for sandboxes
  that never got the chance, not the normal path.
- **Where the agent works.** MachineFactory clones into a local Docker sandbox
  with a Redis sidecar (`REDIS_URL`); factory IPC stays on
  `MEGADESK_FACTORY_REDIS_URL`. CloudFactory runs on a Cursor-hosted VM. Both
  still hand back a PR.
- **Latency.** Redis and a local container start in under a second. A cloud VM
  does not, so a graph that mixes them should expect minutes on those edges.

## Layout

```text
Nodes/Factory/
  MachineFactory/     WORKORDER  → Docker sandbox (+ Redis sidecar) → FINISHED:<repo>
  CloudFactory/       CLOUDORDER → Cursor VM                         → CLOUDFINISHED
```

Each is its own installable node with its own `MegaDesk.nodes` entry point
(`machine_factory`, `cloud_factory`); the nesting groups them, it does not merge
them. Their wire formats are defined once in `megadesk_contracts.wire.machine` and
`megadesk_contracts.wire.cloud`, and every consumer — WorkDispatcher,
VoiceDeck — imports from there.
