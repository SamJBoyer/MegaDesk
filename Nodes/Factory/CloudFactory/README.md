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

MachineFactory also returns a PR, but from a local Docker sandbox (clone + Redis
sidecar) rather than a Cursor-hosted VM. A cloud agent still differs where it
matters:

- Cursor clones the repo **onto its own VM** and pushes a branch itself, so the
  input is a URL. There is no local sandbox or sidecar to manage.
- The agent sees **the pushed remote, not your working tree**. Uncommitted work is
  invisible to it.
- The repo must exist on GitHub with Cursor's GitHub app connected.
- Run ids are durable and prefixed `bc-`, so a restarted BE reattaches by id
  instead of losing track of a running agent. It reattaches with
  `list_runs(agent_id, runtime="cloud")`, because status and PR live on the
  **run**: `agents.get` answers with an `SDKAgentInfo` whose `status` is `None`
  for a cloud agent and which carries no PR, so polling it reports `running`
  forever. `get_run` is no help either — it answers `Run <id> not found` for
  cloud runs. The PR is at `run.git.branches[*].pr_url`.
- There is no `AgentHandler` here. The SDK is the harness, which is the price of
  not owning the machine: less control over the loop, and minutes rather than
  milliseconds to get going.

TicketDispatcher publishes `CLOUDORDER` the same way it publishes `WORKORDER`:
each ticket row picks machine or cloud, and one click feeds that factory.
VoiceDeck can also publish `CLOUDORDER`. This node does not take a GitHub URL
or issue text of its own.

The whole runtime difference is one keyword. Production talks to the SDK through
``AsyncClient.launch_bridge`` (the sync ``Agent.create`` path ``select()``s a
pipe and raises ``WinError 10038`` on Windows):

```python
agent = await client.agents.create(
    model=model, api_key=key,
    cloud=CloudAgentOptions(
        repos=[{"url": url, "startingRef": ref or "dev"}],
        auto_create_pr=True, skip_reviewer_request=True,
    ),
)
```

Empty `ref` is `dev` at launch — MegaDesk's working branch, so a cloud PR
lands where a local one would. Repos only need this branch.

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

A launch that fails with `[validation_error] Failed to verify existence of branch
'<ref>'` is almost never about the ref. Cursor says the same thing for every
branch, including the repository's real default, when the repo is not connected
to the account behind `CURSOR_API_KEY`. Check that before touching the ref:
`client.list_repositories()` comes back empty when the GitHub app is the problem,
and lists the repo when it is not.

## Testing

`FakeCloudFactory` returns `bc-` ids and a canned PR URL, so
`tests/test_cloudfactory_flow.py` exercises the real consumer group, the real
registry and the real canvas without a VM or a pull request. The cut for the launch
options themselves is one level higher — `cloud_launch_options` / `CloudAgentOptions.to_json()`
— because a bare URL in `repos` never reaches the network.

`tests/test_machinefactory_flow.py` is the same suite against the other factory.
