# Integration testing

MegaDesk's breakage is usually at a seam between two modules: a Redis field
renamed on one side, a consumer group that never acks, a widget callback that
stops firing. Unit tests on either side of that seam pass while the seam is
broken. The suite in [`tests/`](../tests/) boots the real canvas, presses the
real widgets, and asserts the real payloads.

Related: [`Docs/node_protocol.md`](node_protocol.md),
[`MegaDesk-Contracts/redis/machine-factory-pipeline.md`](../MegaDesk-Contracts/redis/machine-factory-pipeline.md),
[`MegaDesk-Contracts/redis/voice-chain.md`](../MegaDesk-Contracts/redis/voice-chain.md).

The harness lives in
[`MegaDesk-Contracts/megadesk_contracts/testing/`](../MegaDesk-Contracts/megadesk_contracts/testing/).
It imports nothing from any node — node-specific pieces (which `run_gh` to
patch, which wire helpers to build payloads with) are injected by `conftest`.

---

## Running

```bash
conda activate MEGADESK
pip install -r requirements-dev.txt
redis-server            # any local instance; tests use DBs 14/15
pytest                  # from the repo root
```

Needs a desktop session (Dear PyGui renders; it is not headless) and a local
Redis. Roughly 30 seconds. Selecting subsets:

```bash
pytest tests/test_wire_contract.py   # no GUI, no Redis, no git
pytest -m "not canvas"               # everything that needs no desktop session
pytest tests/test_nodeflow.py -k t6  # one seam
```

`pytest.ini` sets `testpaths` and declares the `canvas` / `redis` / `git`
markers. Never add `pytest-xdist`: one DPG context per process means canvas
tests stay serial.

The suite runs against **this checkout**, not whatever the editable installs
point at: `conftest` puts the repo's source directories at the front of
`sys.path`.

---

## How an agent pilots the GUI

The canvas and every FE run in **one Python process**. Tests drive the same
code a click would — no OS-level input injection, no pixel matching.

| Capability | API |
|---|---|
| Address any widget | `megadesk::{member_id}::{suffix}` via `hosted_node_tag()` |
| Read / write widget state | `dpg.get_value` / `dpg.set_value` |
| Fire the real handler | `dpg.get_item_callback(tag)` then call it |
| Advance time | `dpg.render_dearpygui_frame()` in a controlled loop |
| See the screen | `dpg.output_frame_buffer(path)` |

`show_viewport(minimized=True)` renders nothing. Position the viewport
off-screen instead:

```python
dpg.create_viewport(width=1280, height=800, x_pos=-2400, y_pos=0)
dpg.setup_dearpygui()
dpg.show_viewport()
```

`frame_pump` is a module-global shared by every FE. Arm it relative to
`get_frame_count() + 1`, and call `frame_pump.reset()` when destroying a DPG
context. Coverage: [`tests/test_frame_pump.py`](../tests/test_frame_pump.py),
plus `test_first_node_on_an_empty_board_still_updates` in
[`tests/test_canvas_harness.py`](../tests/test_canvas_harness.py).

---

## Machine pipeline under test

WorkDispatcher → MachineFactory → PRManager. Factory pipeline traffic is on
the process **ephemeral** Redis DB (0 on the live pair; 14 when host pytest
isolates). Supervisor keys live on the persistent half and are not involved.
PRManager does not read Redis: it lists open PRs whose merge-check `mergeable`
status succeeded.

The middle of the chain is not testable in a fast loop: `MachineFactoryManager`
shells out to `docker run` (clone-in-sandbox + Redis sidecar), and
`AgentHandler` calls the real Cursor API. **Cut at the sandbox boundary.**
`FakeAgent` consumes the `WORKORDER` pub/sub signal, stores the payload on the
reference stream the way MachineFactory does, and `XADD`s `FINISHED:{repo}`
with a canned `pr_url`. PRManager is fed mergeable PRs through
`FakeGh`, not that stream. Everything on both sides of that cut stays real.

The real MachineFactory BE is never launched: with no Supervisor alive,
`DisplayEngine._maybe_launch_backend` logs and skips.

Writers emit canonical field names only. Tests assert that set — see
[`tests/test_wire_contract.py`](../tests/test_wire_contract.py).

`WORKORDER` fields: `repo`, `URL`, `ref`, `ticket_name`, `instructions`,
`model`, `auto_pr`, `pictures`, `issue`. `FINISHED:<REPO>` fields: `ticket_name`, `ticket_id`,
`status`, `pr_url`. PRManager shows and opens PRs whose merge-check `mergeable`
status succeeded; it does not consume `FINISHED`.

---

## Harness

```text
MegaDesk-Contracts/megadesk_contracts/testing/
  __init__.py           # public surface
  harness.py            # CanvasHarness
  driver.py             # NodeDriver
  fakes.py              # FakeGh, FakeAgent, FakeCodeAgent, FakeRealtime, factory fakes
  gitfloor.py           # real local git Floor fixture
tests/
  conftest.py
  test_frame_pump.py
  test_canvas_harness.py
  test_nodeflow.py              # WorkDispatcher → PRManager seams
  test_humangate_flow.py        # AutoIntegrate: conflicting PR → order ref
  test_merge_check.py           # mergeable status rollup (no canvas)
  test_vertical_slice.py
  test_node_runtime.py
  test_wire_contract.py
  test_machinefactory_flow.py
  test_cloudfactory_flow.py
  test_codescope_flow.py
  test_voicedeck_flow.py
  test_voice_deck_panel.py
  test_voice_contract.py
  test_workgraph_flow.py
  test_vision_board.py
  test_notepad.py
  test_sargent_flow.py
```

### `CanvasHarness`

| Method | Behavior |
|---|---|
| `boot()` | `build_canvas(model)` with `GraphModel(path=<tmp>)`, off-screen viewport, no Supervisor panel, VoiceDeck chrome on |
| `drop(node_name, position="auto")` | Catalog drop; returns a `NodeDriver` |
| `voice_deck()` | Driver for the always-on VoiceDeck chrome panel |
| `pump(n)` | `engine.sync_members()` + `render_dearpygui_frame()`, n times |
| `wait_until(pred, timeout=10)` | Pumps until true or raises `HarnessTimeout` with a screenshot |
| `screenshot(name)` | Into the per-test artifacts directory |
| `clear_board()` | Deletes every member, running each FE's cleanup |
| `shutdown()` | `clear_board()`, `frame_pump.reset()`, `destroy_context()` |

`wait_until` is required. Every FE in this chain updates through a background
thread → `_ui_queue` → frame-pump drain, so a fixed frame count is a race.

### `NodeDriver`

```python
d = harness.drop("work_dispatcher")
d.type_into("git_url", "https://github.com/acme/widgets")
harness.wait_for_widget(d, f"ticket_btn_{issue_id}")
d.select(f"ticket_factory_{issue_id}", "cloud")
d.select(f"ticket_model_{issue_id}", "grok-4.5")
d.click(f"ticket_btn_{issue_id}")
```

`fire` and `click` go through `dpg.get_item_callback`. Callbacks are invoked
with as many of `(sender, app_data, user_data)` as their signature accepts.

VoiceDeck reaches the same verbs out of process: `list_nodes`, `drop_node`,
`select_node`, `list_widgets`, `get_widget`, `type_into`, `click_widget`, and
`select_widget` publish `CANVAS:CMD` and wait for `CANVAS:REPLY`. The canvas
applies them through `CanvasApi` / `NodeDriver` so typing and clicking are the
real widget callbacks.

### Fixtures

**`FakeGh`** — monkeypatches `run_gh` on both human gates and on
`pr_manager_app` for `gh repo view`, `gh label list`, `gh issue list --label …`,
`gh issue close`, `gh pr list` and `gh pr view`. Issue lists are filtered by
label (`agent-ready` vs whatever the WorkDispatcher dropdown targets).
`add_merge_success` / `add_merge_fail` register open PRs with a `mergeable`
check, the signal merge-check posts.

**`GitFloor`** — a real git Floor in a temp dir. A successful merge test always
pushes, so the fixture includes a pushable local `origin`.

**`FakeAgent`** — consumes through the real `machine_factory` group and builds
FINISHED with `finished_fields` (`status=finished`, canned `pr_url`). No Floor
or worktree.

### Redis isolation

Host pytest points at the **14/15** pair via `REDIS_URL`, set in `conftest`
before any node is imported, and flushes both halves around every test. The
fixtures refuse to flush live 0/1. Sandboxes use Redis sidecars rather than
host DB lanes; if `REDIS_URL` already names a non-live pair, conftest honors it.

---

## Scenarios

[`tests/test_nodeflow.py`](../tests/test_nodeflow.py):

| # | Scenario | Catches |
|---|---|---|
| T1 | Click a ticket row (default factory `machine`). Assert `WORKORDER` gained one entry with the canonical fields (`repo`, `URL`, `ref`, `ticket_name`, `instructions`, `model`, `auto_pr="true"`, `issue`) and `CLOUDORDER` stayed empty. | Field renames; dual-dispatch |
| T1b | Empty issue body dispatches with `instructions` = title | Body/title fallback inverted |
| T1c | Row factory combo `cloud` writes a canonical `CLOUDORDER` and no `WORKORDER` | CloudFactory starved of tickets; dual-dispatch |
| T2 | Row model combo `high` → payload `model` `claude-opus-5` | Per-row widget → payload |
| T2b | `gh repo view` failing surfaces on `status_text` | Errors swallowed |
| T3 | `FakeAgent` consumes; group has zero pending; `FINISHED:{repo}` has the four canonical fields (`ticket_name`, `ticket_id`, `status`, `pr_url`) | Consumer-group and ack |
| T3b | A second pass returns nothing | Redelivery of acked entries |
| T4 | Seed a mergeable PR, pump. Row widgets exist, open-PR / pull / vscode / cursor visible | GitHub list → GUI; frame-pump drain |
| T4b | An `agent-ready` issue is never rendered on PRManager | Queue filter inverted |
| T5 | An unchecked or conflicting PR is not listed on PRManager | Status filter inverted |
| T7 | Click dismiss. Row gone, GitHub PR still open | Local hide vs GUI teardown |
| T9 | Click pull. PR head lands under `PR_SCOPE_ROOT/<repo>/pr-<n>/` | Button → scoped checkout |
| T9b | A second pull hard-resets the same checkout onto a newer PR head | Stale Scope |
| T8 | Full chain in one canvas: dispatch → FakeAgent, mergeable PR row | Two FEs sharing the pump |
| V1 | WorkDispatcher on SMOKETESTREPO → FakeAgent → PRManager | The sandbox row T8 never hosted |

[`tests/test_humangate_flow.py`](../tests/test_humangate_flow.py):

| # | Scenario | Catches |
|---|---|---|
| H1 | The target-label dropdown offers the repo's own labels | A gate that can only ever watch its default |
| H1c | An issue that loses the target label disappears on the next poll | Dead tickets staying on the board |
| H2 | AutoIntegrate reads the PR branch off a failed `mergeable` status and dispatches a `WORKORDER` whose `ref` is that branch | An agent sent to fix a conflict starting from `dev` |
| H2b | The same row on `cloud` puts the branch on `CLOUDORDER.ref` | One factory learning the branch and the other not |
| H2d | A clicked AutoIntegrate PR leaves the bar and stays gone on the next poll | Two agents sent at the same conflict |
| H3 | A PR with no head branch is listed but not dispatchable | Empty-ref orders |
| H4 | A mergeable PR is not on AutoIntegrate; `list_merge_prs` splits success vs failure | The two queues drifting apart |
| H5 | WorkDispatcher leaves `ref` empty, so the factory falls back to `dev` | A default branch quietly becoming mandatory |

Failure artifacts: `wait_until` writes a screenshot on timeout into
`tests/_artifacts/<test name>/`.

Do not fire PRManager's open-PR button in tests: it opens a real browser.
Do not fire `_on_vscode` / `_on_cursor`: they launch the real editor CLIs.

---

## Voice chain

`CodeScope → VoiceDeck → CloudFactory`. Same method; three vendor things stay
out of the loop: a microphone, a Cursor VM, and a billed model. Wire contracts
live in `megadesk_contracts.wire`.

| Fake | Replaces | Left real |
|---|---|---|
| `FakeCodeAgent` | `cursor_sdk` behind CodeScope | HTTP clone + SSE answers (canvas FE); Redis `CODEQ:*` for the local poller |
| `FakeCodeScopeClient` | CodeScope HTTP for VoiceDeck | Tool router, queued SSE, out-of-band injection |
| `FakeRealtime` | OpenAI Realtime socket and both audio devices | Tool router, Redis events, out-of-band answer injection |
| `FakeCloudFactory` | Cursor's VM, branch, pull request | `CLOUDORDER` group, run registry, `CLOUDFINISHED`, retry rules |
| `FakeMachineFactory` | Docker daemon, container, and Redis sidecar | `WORKORDER` group, `AGENTHANDLER`, `FINISHED:<repo>` |

`FakeCodeAgent` has two faces: `runner_factory` feeding canned chunks to the HTTP
`ScopeService`, and `run_once()` as a stand-in for the local Redis poller.

Two timing rules the suite pins:

- VoiceDeck must return `searching` without waiting for SSE.
  `test_asking_returns_searching_immediately_and_queues_the_ask`.
- Control messages from before the BE woke up are ignored.
  `test_a_start_command_from_before_the_backend_woke_up_is_ignored`.

Uncovered on purpose: no socket to OpenAI, no real cloud agent, no
`sounddevice` threads. `python -m VoiceDeckManager devices` and
`python -m CloudFactoryManager models` are the two things to run by hand when
voice is silent or a key is wrong.

---

## Constraints

- Needs a real desktop session. Off-screen viewport works; minimized does not render.
- One DPG context at a time per process.
- Requires Redis at `REDIS_URL` (tests set 14/15 in `conftest`).
- Do not fire `_on_vscode` / `_on_cursor` (they launch the real editor CLIs).
