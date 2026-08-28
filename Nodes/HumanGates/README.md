# Human gates

A human gate is a node where a person approves a step in the graph. Everything
around it is automatic; the gate is the place where a human still says *this
one, now*.

Two of them live here. They are separate nodes because what pressing a row means
is genuinely different, not because the panel is:

| Node | Tracks (default) | Pressing a row |
|------|------------------|----------------|
| WorkDispatcher | `agent-ready` issues | orders a factory to do that ticket |
| AutoIntegrate | `MERGE_FAIL` issues | orders a factory to fix that pull request, on the PR's own branch |

## What they share

The panel, and only the panel: a repo URL with a connection lamp, a **target
label** dropdown filled from the labels that actually exist on the connected
repo, and a list of the open issues carrying that label. The label is the gate's
target, saved into the graph as `ISSUE_LABEL`, so a graph reopens pointed at the
same queue.

The reading half is shared code — `megadesk_contracts.human_gate` — because
"which labels does this repo have" and "which issues carry this one" should have
one answer. The deciding half is not shared, and there is no base class: these
are the same idea wearing two different backends, and forcing them through one
would only move the difference somewhere less visible.

```text
Nodes/HumanGates/
  WorkDispatcher/   agent-ready issue  → WORKORDER / CLOUDORDER (ref: default)
  AutoIntegrate/    MERGE_FAIL issue   → WORKORDER / CLOUDORDER (ref: the PR branch)
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

AutoIntegrate learns the branch from the issue itself.
`.github/workflows/merge-check.yml` writes the pull request number, its head
branch and its base into the issue body as markers, and puts the branch in the
title so the issue is readable at a glance. The markers are spelled once, in
`megadesk_contracts.human_gate`, and read back by AutoIntegrate and PRManager.
An issue filed before those markers existed still works: the gate asks GitHub
for the head branch of the PR number it did find.
