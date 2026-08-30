# Human gates

A human gate is a node where a person approves a step in the graph. Everything
around it is automatic; the gate is the place where a human still says *this
one, now*.

Two of them live here. They are separate nodes because what pressing a row means
is genuinely different, not because the panel is:

| Node | Tracks | Pressing a row |
|------|--------|----------------|
| WorkDispatcher | `agent-ready` issues (or whichever label the operator picks) | orders a factory to do that ticket |
| AutoIntegrate | open PRs whose merge-check `mergeable` status failed | orders a factory to fix that pull request, on the PR's own branch |

## What they share

A repo URL with a connection lamp, and a list of tickets. WorkDispatcher also
has a **target label** dropdown filled from the labels that actually exist on
the connected repo, saved into the graph as `ISSUE_LABEL`. Both gates have a
**depth** field that sizes the node to fit that many tickets. AutoIntegrate has
no label: merge-check's fail signal *is* its queue. Clicking a WorkDispatcher
ticket dispatches it and moves the GitHub issue from `agent-ready` to
`in-progress`.

The GitHub reading half is shared code — `megadesk_contracts.human_gate` —
because "which labels does this repo have", "which issues carry this one", and
"which PRs did merge-check mark mergeable / conflicting" should each have one
answer. The deciding half is not shared, and there is no base class: these are
the same idea wearing two different backends, and forcing them through one
would only move the difference somewhere less visible.

```text
Nodes/HumanGates/
  WorkDispatcher/   agent-ready issue  → WORKORDER / CLOUDORDER (ref: default)
  AutoIntegrate/    mergeable=failure  → WORKORDER / CLOUDORDER (ref: the PR branch)
```

Each is its own installable node with its own `MegaDesk.nodes` entry point
(`work_dispatcher`, `auto_integrate`); the nesting groups them, it does not merge
them.

## Why AutoIntegrate needs a branch

A merge conflict lives on the pull request's branch, so an agent sent to fix one
has to stand on that branch. Both factories therefore take an optional `ref` on
the order and fall back to `dev` when it is absent — see
`megadesk_contracts.wire.factory.DEFAULT_STARTING_REF`. WorkDispatcher never
sets it; AutoIntegrate always does.

AutoIntegrate learns the branch from the PR itself.
`.github/workflows/merge-check.yml` posts a check named
`mergeable` onto each PR head (and re-posts when `dev` moves).
`list_merge_prs` in `megadesk_contracts.human_gate` is how AutoIntegrate and
PRManager both read that signal.
