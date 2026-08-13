# CloudDispatcher

Send a Cursor cloud agent to make a small documentation change and open a pull
request. One order in, one PR link out.

## Halves

| Half | What it does |
|------|--------------|
| FE (`cloud_dispatcher_frontend/app.py`) | Type an order, approve drafts, watch runs, open the PR |
| BE (`CloudDispatcherManager/`) | Consume `CLOUDORDER`, launch cloud agents, follow them, publish `CLOUDFINISHED` |

## How a cloud agent differs from the local ones

MissionControl runs agents on this machine, in a worktree, and MergeManager
merges the result. A cloud agent is the opposite of that in every way that
matters:

- Cursor clones the repo **onto its own VM** and pushes a branch itself, so the
  input is a URL. There is no worktree to hand over and nothing to merge.
- The agent sees **the pushed remote, not your working tree**. Uncommitted work
  is invisible to it.
- The repo must exist on GitHub with Cursor's GitHub app connected.
- Run ids are durable and prefixed `bc-`, so a restarted BE reattaches with
  `Agent.get` instead of losing track of a running agent.

The whole runtime difference is one keyword:

```python
# local, as MissionControl's handler.py does today
Agent.create(model=model, api_key=key, local=LocalAgentOptions(cwd=workspace))

# cloud, as this node does
Agent.create(model=model, api_key=key,
             cloud=CloudAgentOptions(repos=[url], auto_create_pr=True,
                                     skip_reviewer_request=True))
```

Always pass exactly one of `local` / `cloud` explicitly. With neither set the SDK
quietly defaults to local, which would run a "cloud" documentation job on your
own machine — the kind of bug that costs an afternoon to notice.

## Wire

| Where | Key | Carries |
|-------|-----|---------|
| db 0 stream | `CLOUDORDER` | `order_id`, `repo_url`, `ref`, `title`, `instructions`, `model`, `auto_pr` |
| db 0 stream | `CLOUDFINISHED` | `agent_id`, `order_id`, `status`, `pr_url` |
| db 1 hash | `CLOUDRUN:<agent_id>` | `order_id`, `repo_url`, `title`, `status`, `pr_url`, `run_id` |
| db 1 hash | `CLOUDDRAFT:<order_id>` | the `CLOUDORDER` field set, held back |

All defined once in `megadesk_contracts.wire.cloud` and imported by both halves.

The registry on db 1 is the source of truth, not anything held in memory. Orders
launch in under a second; the runs they start take minutes and outlive any
process here, so `CLOUDRUN:<agent_id>` is written **before** the order is acked
and its status is what makes `CLOUDFINISHED` fire exactly once.

## Drafts

A draft is an order nobody has agreed to yet. VoiceDeck writes one rather than
publishing `CLOUDORDER`, because a misheard sentence should not be able to open a
pull request; the FE shows it as a row with a `go` button and nothing happens
until it is pressed. Pressing it publishes the stored fields and deletes the
hash immediately, so an impatient second click cannot mean two PRs.

Typing an order into the FE skips the draft step, because typing the instructions
*is* the confirmation.

## The two failure modes, kept apart

`AgentStartupError` means the run never started — auth, config, rate limit — and
reports as `startup_error` with no `agent_id`, because none exists. A run that
started and failed reports as `error` with its id, and the transcript is what to
look at. Only the first is retried, and only when Cursor says it is retryable:
`retry_after` is honored, and after three attempts the order is reported rather
than retried forever. A blind retry of a launch that actually succeeded would
open a second pull request.

## Requirements

- `CURSOR_API_KEY`, and a GitHub repo with Cursor's GitHub app connected
- `pip install -e Nodes/CloudDispatcher[canvas]`

`python -m CloudDispatcherManager models` lists the models the account can use —
the fastest way to check the key works. `python -m CloudDispatcherManager runs`
prints the registry, which is what the FE renders. The model combo is populated
from the same call rather than hardcoded, and only when a key is present.

## Testing

`FakeCloudRuntime` returns `bc-` ids and a canned PR URL, so
`tests/test_clouddispatch_flow.py` exercises the real consumer group, the real
registry and the real canvas without a VM or a pull request. The cut for the
launch options themselves is one level higher — `CursorCloudRuntime._sdk` — since
the bug worth guarding against there is a missing keyword argument, not a bad
response.
