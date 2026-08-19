# CloudFactory

Sends Cursor cloud agents to work on a repo and open a pull request. One order in,
one PR link out.

The local counterpart is [MachineFactory](../MachineFactory/README.md); what the
two share, and where they honestly differ, is in [Factory](../README.md).

## Halves

| Half | What it does |
|------|--------------|
| FE (`cloud_factory_frontend/app.py`) | Queued CLOUDORDERs, live agents, error lamp |
| BE (`CloudFactoryManager/`) | Consume `CLOUDORDER`, launch cloud agents, follow them, publish `CLOUDFINISHED` |

## How a cloud agent differs from a local one

MachineFactory runs agents on this machine, in a worktree, and MergeManager merges
the result. A cloud agent is the opposite of that in every way that matters:

- Cursor clones the repo **onto its own VM** and pushes a branch itself, so the
  input is a URL. There is no worktree to hand over and nothing to merge.
- The agent sees **the pushed remote, not your working tree**. Uncommitted work is
  invisible to it.
- The repo must exist on GitHub with Cursor's GitHub app connected.
- Run ids are durable and prefixed `bc-`, so a restarted BE reattaches with
  `Agent.get` instead of losing track of a running agent.
- There is no `AgentHandler` here. The SDK is the harness, which is the price of
  not owning the machine: less control over the loop, and minutes rather than
  milliseconds to get going.

TicketDispatcher publishes `CLOUDORDER` the same way it publishes `WORKORDER`:
one click on an agent-ready issue feeds both factories. VoiceDeck can also
publish `CLOUDORDER`. This node does not take a GitHub URL or issue text of its
own.

The whole runtime difference is one keyword. Production talks to the SDK through
``AsyncClient.launch_bridge`` (the sync ``Agent.create`` path ``select()``s a
pipe and raises ``WinError 10038`` on Windows):

```python
agent = await client.agents.create(
    model=model, api_key=key,
    cloud=CloudAgentOptions(repos=[url], auto_create_pr=True,
                            skip_reviewer_request=True),
)
```

## Wire

| Where | Key | Carries |
|-------|-----|---------|
| db 0 stream | `CLOUDORDER` | `order_id`, `repo_url`, `ref`, `title`, `instructions`, `model`, `auto_pr` |
| db 0 stream | `CLOUDFINISHED` | `agent_id`, `order_id`, `status`, `pr_url` |
| db 1 hash | `CLOUDRUN:<agent_id>` | `order_id`, `repo_url`, `title`, `status`, `pr_url`, `run_id` |

All defined once in `megadesk_contracts.wire.cloud`, on the shared status
vocabulary in `megadesk_contracts.wire.factory`, and imported by both halves.

The registry on db 1 is the source of truth, not anything held in memory. Orders
launch in under a second; the runs they start take minutes and outlive any process
here, so `CLOUDRUN:<agent_id>` is written **before** the order is acked and its
status is what makes `CLOUDFINISHED` fire exactly once.

## The two failure modes, kept apart

`AgentStartupError` means the run never started — auth, config, rate limit — and
reports as `startup_error` with no `agent_id`, because none exists. A run that
started and failed reports as `error` with its id, and the transcript is what to
look at. Only the first is retried, and only when Cursor says it is retryable:
`retry_after` is honored, and after three attempts the order is reported rather
than retried forever. A blind retry of a launch that actually succeeded would open
a second pull request.

## Requirements

- `CURSOR_API_KEY`, and a GitHub repo with Cursor's GitHub app connected
- `pip install -e Nodes/Factory/CloudFactory[canvas]`

`python -m CloudFactoryManager models` lists the models the account can use — the
fastest way to check the key works. `python -m CloudFactoryManager runs` prints the
registry, which is what the FE renders.

## Testing

`FakeCloudFactory` returns `bc-` ids and a canned PR URL, so
`tests/test_cloudfactory_flow.py` exercises the real consumer group, the real
registry and the real canvas without a VM or a pull request. The cut for the launch
options themselves is one level higher — `CursorCloudFactory._async_launch` / the
`cloud=` options — since the bug worth guarding against there is a missing
keyword argument, not a bad response.

`tests/test_machinefactory_flow.py` is the same suite against the other factory.
