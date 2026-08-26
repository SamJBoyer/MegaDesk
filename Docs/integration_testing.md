# Integration testing

MegaDesk's breakage is usually at a seam between two modules: a Redis field
renamed on one side, a consumer group that never acks, a widget callback that
stops firing. Unit tests on either side of that seam pass while the seam is
broken. The suite in [`tests/`](../tests/) boots the real canvas, presses the
real widgets, and asserts the real payloads.

Related: [`Docs/node_protocol.md`](node_protocol.md),
[`MegaDesk-contracts/redis/machine-factory-pipeline.md`](../MegaDesk-contracts/redis/machine-factory-pipeline.md),
[`MegaDesk-contracts/redis/voice-chain.md`](../MegaDesk-contracts/redis/voice-chain.md).

The harness lives in
[`MegaDesk-contracts/megadesk_contracts/testing/`](../MegaDesk-contracts/megadesk_contracts/testing/).
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

TicketDispatcher → MachineFactory → MergeManager. All pipeline traffic is on
the process **ephemeral** Redis DB (0 on the live pair; 14 when host pytest
isolates). Supervisor keys live on the persistent half and are not involved.

The middle of the chain is not testable in a fast loop: `MachineFactoryManager`
shells out to `docker run` (clone-in-sandbox + Redis sidecar), and
`AgentHandler` calls the real Cursor API. **Cut at the sandbox boundary.**
`FakeAgent` consumes `WORKORDER` using the real `machine_factory` consumer
group and `XADD`s `FINISHED:{repo}` with a canned `pr_url`. Everything on both
sides of that cut stays real.

The real MachineFactory BE is never launched: with no Supervisor alive,
`DisplayEngine._maybe_launch_backend` logs and skips.

Writers emit canonical field names only. Tests assert that set — see
[`tests/test_wire_contract.py`](../tests/test_wire_contract.py).

`WORKORDER` fields: `repo`, `URL`, `ticket_name`, `instructions`, `model`,
`auto_pr`. `FINISHED:<REPO>` fields: `ticket_name`, `ticket_id`, `status`,
`pr_url`. MergeManager shows and opens PRs; it does not merge local trees.

---

## Harness

```text
MegaDesk-contracts/megadesk_contracts/testing/
  __init__.py           # public surface
  harness.py            # CanvasHarness
  driver.py             # NodeDriver
  fakes.py              # FakeGh, FakeAgent, FakeCodeAgent, FakeRealtime, factory fakes
  gitfloor.py           # real local git Floor fixture
tests/
  conftest.py
  test_frame_pump.py
  test_canvas_harness.py
  test_nodeflow.py              # TicketDispatcher → MergeManager seams
  test_vertical_slice.py
  test_node_runtime.py
  test_wire_contract.py
  test_machinefactory_flow.py
  test_cloudfactory_flow.py
  test_codescope_flow.py
  test_voicedeck_flow.py
  test_voice_contract.py
  test_workgraph_flow.py
```

### `CanvasHarness`

| Method | Behavior |
|---|---|
| `boot()` | `build_canvas(model)` with `GraphModel(path=<tmp>)`, off-screen viewport, no Supervisor panel |
| `drop(node_name, position="auto")` | Catalog drop; returns a `NodeDriver` |
| `pump(n)` | `engine.sync_members()` + `render_dearpygui_frame()`, n times |
| `wait_until(pred, timeout=10)` | Pumps until true or raises `HarnessTimeout` with a screenshot |
| `screenshot(name)` | Into the per-test artifacts directory |
| `clear_board()` | Deletes every member, running each FE's cleanup |
| `shutdown()` | `clear_board()`, `frame_pump.reset()`, `destroy_context()` |

`wait_until` is required. Every FE in this chain updates through a background
thread → `_ui_queue` → frame-pump drain, so a fixed frame count is a race.

### `NodeDriver`

```python
d = harness.drop("ticket_dispatcher")
d.type_into("git_url", "https://github.com/acme/widgets")
harness.wait_for_widget(d, f"ticket_btn_{issue_id}")
d.select(f"ticket_model_{issue_id}", "grok-4.5")
d.click(f"ticket_btn_{issue_id}")
```

`fire` and `click` go through `dpg.get_item_callback`. Callbacks are invoked
with as many of `(sender, app_data, user_data)` as their signature accepts.

### Fixtures

**`FakeGh`** — monkeypatches `ticket_dispatcher_app.run_gh` for `gh repo view`
and `gh issue list --label agent-ready`.

**`GitFloor`** — a real git Floor in a temp dir. `attempt_merge` always pushes
on success, so the fixture includes a pushable local `origin`.

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
| T1 | Click a ticket row. Assert `WORKORDER` gained one entry with the six canonical fields (`repo`, `URL`, `ticket_name`, `instructions`, `model`, `auto_pr="true"`). | Field renames |
| T1b | Empty issue body dispatches with `instructions` = title | Body/title fallback inverted |
| T1c | Same click also writes a canonical `CLOUDORDER` | CloudFactory starved of tickets |
| T2 | Row model combo `grok-4.5` → payload `model` | Per-row widget → payload |
| T2b | `gh repo view` failing surfaces on `status_text` | Errors swallowed |
| T3 | `FakeAgent` consumes; group has zero pending; `FINISHED:{repo}` has the four canonical fields (`ticket_name`, `ticket_id`, `status`, `pr_url`) | Consumer-group and ack |
| T3b | A second pass returns nothing | Redelivery of acked entries |
| T4 | `XADD FINISHED:{repo}`, pump. Row widgets exist, open-PR button visible | Stream → GUI; frame-pump drain |
| T4b | A FINISHED entry missing fields is acked and never rendered | Poison entries retried forever |
| T5 | Error FINISHED without `pr_url`: open-PR disabled | Empty-URL affordance |
| T7 | Click dismiss. `XACK` + `XDEL`, row gone | Stream cleanup vs GUI teardown |
| T8 | Full chain in one canvas: dispatch → FakeAgent → PR row | Two FEs sharing the pump |
| V1 | TicketDispatcher on SMOKETESTREPO → FakeAgent → MergeManager | The sandbox row T8 never hosted |

Failure artifacts: `wait_until` writes a screenshot on timeout into
`tests/_artifacts/<test name>/`.

Do not fire MergeManager's editor buttons: they `Popen(..., shell=True)` and
would launch real editors.

---

## Voice chain

`CodeScope → VoiceDeck → CloudFactory`. Same method; three vendor things stay
out of the loop: a microphone, a Cursor VM, and a billed model. Wire contracts
live in `megadesk_contracts.wire`.

| Fake | Replaces | Left real |
|---|---|---|
| `FakeCodeAgent` | `cursor_sdk` behind CodeScope | Clone on disk, `CODEQ:ASK` group, session hash, every `CODEQ:ANSWER` |
| `FakeRealtime` | OpenAI Realtime socket and both audio devices | Tool router, Redis events, out-of-band answer injection |
| `FakeCloudFactory` | Cursor's VM, branch, pull request | `CLOUDORDER` group, run registry, `CLOUDFINISHED`, retry rules |
| `FakeMachineFactory` | Docker daemon, container, and Redis sidecar | `WORKORDER` group, `AGENTHANDLER`, `FINISHED:<repo>` |

`FakeCodeAgent` has two faces: `run_once()` as a stand-in BE for FE tests, and
`runner_factory` feeding canned chunks to the real `CodeScopeManager`.

`CODESCOPE:SESSION:<id>`, `CLOUDRUN:<agent_id>` live
on the **persistent** DB. Host pytest owns 14/15 and flushes both.

Two timing rules the suite pins:

- Answers must not be read from `$` on every poll.
  `test_an_answer_that_lands_between_polls_is_not_missed`.
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
- Do not fire `_on_vscode` / `_on_cursor`.
