"""CloudDispatcher end to end, without spending money or opening a real PR.

``FakeCloudRuntime`` stands in for Cursor's VM; everything either side of it is
real — the CLOUDORDER consumer group, the run registry on db 1, the CLOUDFINISHED
payloads, and the canvas widgets that turn a draft into an order.

The assertions cluster around one risk. Every other failure here is cosmetic, but
launching twice for one order means two pull requests, so duplicate suppression,
the retry rules, and the draft's single click get tested harder than the rest.
"""

from __future__ import annotations

import pytest
from conftest import (
    CLOUDFINISHED_CANONICAL_FIELDS,
    CLOUDORDER_CANONICAL_FIELDS,
    CLOUDORDER_GROUP,
    CLOUDRUN_CANONICAL_FIELDS,
)
from megadesk_contracts import AgentStartupError
from megadesk_contracts.wire import cloud as wire

pytestmark = pytest.mark.redis

REPO_URL = "https://github.com/acme/widgets"
INSTRUCTIONS = "Explain in the README why the frame pump needs a reset."
TITLE = "Document the frame pump reset"


# --- helpers ---------------------------------------------------------------


def place_order(
    redis_client,
    *,
    order_id: str = "",
    repo_url: str = REPO_URL,
    title: str = TITLE,
    instructions: str = INSTRUCTIONS,
    auto_pr: bool = True,
) -> str:
    order_id = order_id or wire.new_order_id()
    redis_client.xadd(
        wire.CLOUDORDER_STREAM,
        wire.cloudorder_fields(
            order_id=order_id,
            repo_url=repo_url,
            title=title,
            instructions=instructions,
            auto_pr=auto_pr,
        ),
    )
    return order_id


def seed_draft(persistent_client, *, order_id: str = "", title: str = TITLE) -> str:
    """Write the draft VoiceDeck would write: an order nobody agreed to yet."""
    order_id = order_id or wire.new_order_id()
    persistent_client.hset(
        wire.clouddraft_key(order_id),
        mapping=wire.cloudorder_fields(
            order_id=order_id,
            repo_url=REPO_URL,
            title=title,
            instructions=INSTRUCTIONS,
        ),
    )
    return order_id


def seed_run(
    persistent_client,
    *,
    agent_id: str = "bc-seeded01",
    status: str = wire.STATUS_RUNNING,
    pr_url: str = "",
    title: str = TITLE,
) -> str:
    persistent_client.hset(
        wire.cloudrun_key(agent_id),
        mapping=wire.cloudrun_fields(
            order_id=wire.new_order_id(),
            repo_url=REPO_URL,
            title=title,
            status=status,
            pr_url=pr_url,
        ),
    )
    return agent_id


def runs_on(persistent_client) -> dict[str, dict[str, str]]:
    return {
        wire.agent_id_from_key(key): persistent_client.hgetall(key)
        for key in persistent_client.scan_iter(match=f"{wire.CLOUDRUN_PREFIX}*")
    }


def finished(read_stream) -> list[dict[str, str]]:
    return [fields for _entry_id, fields in read_stream(wire.CLOUDFINISHED_STREAM)]


def pending_orders(redis_client) -> int:
    info = redis_client.xpending(wire.CLOUDORDER_STREAM, CLOUDORDER_GROUP)
    count = info.get("pending") if isinstance(info, dict) else info[0]
    return int(count or 0)


# --- launching -------------------------------------------------------------


def test_an_order_launches_one_agent_and_registers_it(
    cloud_dispatcher, fake_cloud_runtime, redis_client, persistent_client, read_stream
) -> None:
    order_id = place_order(redis_client)

    assert cloud_dispatcher.poll_orders() == 1

    assert len(fake_cloud_runtime.launches) == 1
    launch = fake_cloud_runtime.launches[0]
    assert launch["repo_url"] == REPO_URL
    assert launch["auto_pr"] is True
    assert INSTRUCTIONS in launch["instructions"]

    registered = runs_on(persistent_client)
    assert list(registered) == [launch["agent_id"]]
    fields = registered[launch["agent_id"]]
    assert set(fields) == set(CLOUDRUN_CANONICAL_FIELDS)
    assert fields["order_id"] == order_id
    assert fields["status"] == wire.STATUS_RUNNING
    assert fields["pr_url"] == ""
    assert finished(read_stream) == [], "a launched run has not finished"
    assert pending_orders(redis_client) == 0, "a handled order must not stay pending"


def test_the_same_order_twice_still_launches_once(
    cloud_dispatcher, fake_cloud_runtime, redis_client, persistent_client
) -> None:
    """Two launches for one order means two pull requests for one request."""
    order_id = place_order(redis_client)
    cloud_dispatcher.poll_orders()
    place_order(redis_client, order_id=order_id)
    cloud_dispatcher.poll_orders()

    assert len(fake_cloud_runtime.launches) == 1
    assert len(runs_on(persistent_client)) == 1


def test_the_draft_is_cleared_once_its_order_runs(
    cloud_dispatcher, redis_client, persistent_client
) -> None:
    order_id = seed_draft(persistent_client)
    place_order(redis_client, order_id=order_id)

    cloud_dispatcher.poll_orders()

    assert persistent_client.exists(wire.clouddraft_key(order_id)) == 0


def test_an_unusable_order_is_acked_rather_than_retried_forever(
    cloud_dispatcher, fake_cloud_runtime, redis_client
) -> None:
    redis_client.xadd(wire.CLOUDORDER_STREAM, {"order_id": "", "repo_url": ""})

    assert cloud_dispatcher.poll_orders() == 0
    assert fake_cloud_runtime.launches == []
    assert pending_orders(redis_client) == 0


# --- the two failure modes -------------------------------------------------


def test_a_launch_that_never_started_is_reported_with_no_agent(
    cloud_dispatcher, fake_cloud_runtime, redis_client, persistent_client, read_stream
) -> None:
    """No agent id exists, so the report cannot carry one — and must not invent one."""
    fake_cloud_runtime.startup_error = "no CURSOR_API_KEY in the environment"
    order_id = place_order(redis_client)

    assert cloud_dispatcher.poll_orders() == 0

    reports = finished(read_stream)
    assert len(reports) == 1
    assert set(reports[0]) == set(CLOUDFINISHED_CANONICAL_FIELDS)
    assert reports[0]["status"] == wire.STATUS_STARTUP_ERROR
    assert reports[0]["order_id"] == order_id
    assert reports[0]["agent_id"] == ""
    assert runs_on(persistent_client) == {}
    assert pending_orders(redis_client) == 0


def test_a_retryable_failure_is_retried_and_still_launches_only_once(
    cloud_dispatcher, fake_cloud_runtime, redis_client, persistent_client, read_stream
) -> None:
    """Cursor's own advice decides this; a blind retry could double-launch."""
    fake_cloud_runtime.startup_error = "rate limited"
    fake_cloud_runtime.retryable = True
    place_order(redis_client)

    assert cloud_dispatcher.poll_orders() == 0
    assert finished(read_stream) == [], "a retryable failure is not an outcome yet"
    assert pending_orders(redis_client) == 1, "the order must stay claimed to be retried"

    fake_cloud_runtime.startup_error = ""
    assert cloud_dispatcher.poll_orders() == 1

    assert len(fake_cloud_runtime.launches) == 1
    assert len(runs_on(persistent_client)) == 1


def test_a_retryable_failure_gives_up_and_reports(
    cloud_dispatcher, fake_cloud_runtime, redis_client, read_stream
) -> None:
    fake_cloud_runtime.startup_error = "rate limited"
    fake_cloud_runtime.retryable = True
    place_order(redis_client)

    for _attempt in range(4):
        cloud_dispatcher.poll_orders()

    reports = finished(read_stream)
    assert len(reports) == 1, "one order produces one outcome, however many attempts"
    assert reports[0]["status"] == wire.STATUS_STARTUP_ERROR
    assert pending_orders(redis_client) == 0


def test_a_run_that_ran_and_failed_is_not_a_startup_error(
    cloud_dispatcher, fake_cloud_runtime, redis_client, persistent_client, read_stream
) -> None:
    """Different fix, different retry advice: the transcript is what to look at."""
    fake_cloud_runtime.run_error = "the agent could not find the file"
    fake_cloud_runtime.polls_before_finish = 0
    place_order(redis_client)
    cloud_dispatcher.poll_orders()

    assert cloud_dispatcher.poll_runs() == 1

    reports = finished(read_stream)
    assert len(reports) == 1
    assert reports[0]["status"] == wire.STATUS_ERROR
    assert reports[0]["agent_id"].startswith(wire.CLOUD_AGENT_ID_PREFIX)
    assert reports[0]["pr_url"] == ""
    agent_id = reports[0]["agent_id"]
    assert runs_on(persistent_client)[agent_id]["status"] == wire.STATUS_ERROR


# --- following a run to its pull request -----------------------------------


def test_a_finished_run_is_reported_once_with_its_pr_link(
    cloud_dispatcher, fake_cloud_runtime, redis_client, persistent_client, read_stream
) -> None:
    place_order(redis_client)
    cloud_dispatcher.poll_orders()
    agent_id = fake_cloud_runtime.launches[0]["agent_id"]

    # Still working: the registry updates, but nothing is announced.
    assert cloud_dispatcher.poll_runs() == 0
    assert runs_on(persistent_client)[agent_id]["status"] == wire.STATUS_RUNNING
    assert finished(read_stream) == []

    assert cloud_dispatcher.poll_runs() == 1

    reports = finished(read_stream)
    assert len(reports) == 1
    assert set(reports[0]) == set(CLOUDFINISHED_CANONICAL_FIELDS)
    assert reports[0]["status"] == wire.STATUS_FINISHED
    assert reports[0]["agent_id"] == agent_id
    assert "/pull/" in reports[0]["pr_url"]

    stored = runs_on(persistent_client)[agent_id]
    assert stored["status"] == wire.STATUS_FINISHED
    assert stored["pr_url"] == reports[0]["pr_url"]

    # Polled again, a finished run says nothing: the hash is what makes it once,
    # which is also what survives a restart of this process.
    assert cloud_dispatcher.poll_runs() == 0
    assert len(finished(read_stream)) == 1


def test_only_unfinished_runs_are_polled(
    cloud_dispatcher, fake_cloud_runtime, persistent_client
) -> None:
    """A finished run asked about again is a rate limit spent on old news."""
    seed_run(persistent_client, agent_id="bc-done01", status=wire.STATUS_FINISHED)
    live = seed_run(persistent_client, agent_id="bc-live01")

    assert [agent_id for agent_id, _run in cloud_dispatcher.live_runs()] == [live]


def test_cancelling_a_run_stops_it_and_says_so(
    cloud_dispatcher, fake_cloud_runtime, redis_client, persistent_client, read_stream
) -> None:
    place_order(redis_client)
    cloud_dispatcher.poll_orders()
    agent_id = fake_cloud_runtime.launches[0]["agent_id"]

    assert cloud_dispatcher.cancel(agent_id) is True

    assert fake_cloud_runtime.cancelled == [agent_id]
    assert runs_on(persistent_client)[agent_id]["status"] == wire.STATUS_CANCELLED
    assert finished(read_stream)[-1]["status"] == wire.STATUS_CANCELLED
    assert cloud_dispatcher.poll_runs() == 0, "a cancelled run is already accounted for"


# --- the real runtime's cloud options --------------------------------------


class _StubOptions:
    """Stands in for ``CloudAgentOptions``, recording what it was handed."""

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs


class _StubRun:
    id = "run-1"


class _StubAgent:
    agent_id = "bc-stub001"
    created: dict = {}
    prompts: list[str] = []

    @classmethod
    def create(cls, **kwargs):
        cls.created = kwargs
        return cls()

    def send(self, prompt: str):
        type(self).prompts.append(prompt)
        return _StubRun()


def test_the_cloud_runtime_asks_for_a_pr_and_never_runs_locally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SDK defaults to local when neither runtime is set, which would be silent.

    Cut above ``cursor_sdk`` rather than at the network, because the bug this
    guards against is a missing keyword argument, not a bad response.
    """
    from CloudDispatcherManager.runtime import CursorCloudRuntime

    _StubAgent.created, _StubAgent.prompts = {}, []
    runtime = CursorCloudRuntime(api_key="key")
    monkeypatch.setattr(runtime, "_sdk", lambda: (_StubAgent, _StubOptions))

    launch = runtime.launch(
        repo_url=REPO_URL, instructions=INSTRUCTIONS, title=TITLE, model="auto"
    )

    assert launch.agent_id == "bc-stub001"
    assert launch.run_id == "run-1"
    created = _StubAgent.created
    assert "local" not in created, "a 'cloud' job must not run on this machine"
    options = created["cloud"].kwargs
    assert options["repos"] == [REPO_URL]
    assert options["auto_create_pr"] is True
    # An unattended run that pages a human at 3am is worse than no run.
    assert options["skip_reviewer_request"] is True
    assert INSTRUCTIONS in _StubAgent.prompts[0]
    assert TITLE in _StubAgent.prompts[0]


def test_an_unknown_status_is_treated_as_still_running() -> None:
    """Guessing 'finished' would close a run that is still writing to a branch."""
    from CloudDispatcherManager.runtime import normalize_status

    assert normalize_status("CREATING") == wire.STATUS_RUNNING
    assert normalize_status("something new") == wire.STATUS_RUNNING
    assert normalize_status("") == wire.STATUS_RUNNING
    assert normalize_status("FINISHED") == wire.STATUS_FINISHED
    assert normalize_status("failed") == wire.STATUS_ERROR


def test_a_launch_failure_keeps_cursors_retry_advice() -> None:
    """Dropping ``retryable`` would turn a rate limit into a lost order."""
    from CloudDispatcherManager.runtime import CursorCloudRuntime

    class Refusal(Exception):
        message = "rate limited"
        is_retryable = True
        retry_after = 12

    error = CursorCloudRuntime._startup_error(Refusal(), "launch refused")
    assert isinstance(error, AgentStartupError)
    assert error.retryable is True
    assert error.retry_after == 12
    assert "rate limited" in str(error)


# --- the frontend ----------------------------------------------------------


@pytest.mark.canvas
def test_typing_an_order_publishes_a_canonical_cloudorder(
    harness, redis_client, read_stream
) -> None:
    fe = harness.drop("cloud_dispatcher")
    fe.set("repo_url", REPO_URL)
    fe.type_into("instructions", INSTRUCTIONS)

    orders = read_stream(wire.CLOUDORDER_STREAM)
    assert len(orders) == 1
    _entry_id, order = orders[0]
    assert set(order) == set(CLOUDORDER_CANONICAL_FIELDS)
    assert order["repo_url"] == REPO_URL
    assert order["instructions"] == INSTRUCTIONS
    assert order["auto_pr"] == "true"
    assert order["title"], "a PR needs a title, so one is derived"
    assert fe.get("instructions") == "", "the input clears once the order is sent"


@pytest.mark.canvas
def test_an_order_with_no_repository_is_refused(
    harness, redis_client, read_stream
) -> None:
    fe = harness.drop("cloud_dispatcher")
    fe.type_into("instructions", INSTRUCTIONS)

    assert read_stream(wire.CLOUDORDER_STREAM) == []
    assert "repository" in fe.get("status_text")


@pytest.mark.canvas
def test_a_draft_becomes_an_order_on_one_click(
    harness, redis_client, persistent_client, read_stream
) -> None:
    """This is the rail that keeps a misheard sentence from opening a PR."""
    order_id = seed_draft(persistent_client)
    fe = harness.drop("cloud_dispatcher")

    harness.wait_until(
        lambda: fe.exists(f"draft_text_{order_id}"),
        message="the draft row to appear",
    )
    assert fe.get(f"draft_text_{order_id}") == TITLE
    assert read_stream(wire.CLOUDORDER_STREAM) == [], "a draft on its own does nothing"

    fe.click(f"draft_go_{order_id}")

    orders = read_stream(wire.CLOUDORDER_STREAM)
    assert len(orders) == 1
    assert orders[0][1]["order_id"] == order_id
    assert set(orders[0][1]) == set(CLOUDORDER_CANONICAL_FIELDS)
    # Gone from both the row and db 1, so an impatient second click cannot
    # produce a second pull request.
    assert not fe.exists(f"draft_go_{order_id}")
    assert persistent_client.exists(wire.clouddraft_key(order_id)) == 0


@pytest.mark.canvas
def test_discarding_a_draft_publishes_nothing(
    harness, redis_client, persistent_client, read_stream
) -> None:
    order_id = seed_draft(persistent_client)
    fe = harness.drop("cloud_dispatcher")
    harness.wait_until(
        lambda: fe.exists(f"draft_del_{order_id}"), message="the draft row to appear"
    )

    fe.click(f"draft_del_{order_id}")

    assert read_stream(wire.CLOUDORDER_STREAM) == []
    assert persistent_client.exists(wire.clouddraft_key(order_id)) == 0
    assert not fe.exists(f"draft_row_{order_id}")


@pytest.mark.canvas
def test_a_run_shows_its_status_and_offers_its_pr_when_there_is_one(
    harness, redis_client, persistent_client, opened_urls: list[str]
) -> None:
    agent_id = seed_run(persistent_client)
    fe = harness.drop("cloud_dispatcher")

    harness.wait_until(
        lambda: fe.exists(f"run_text_{agent_id}"), message="the run row to appear"
    )
    assert wire.STATUS_RUNNING in fe.get(f"run_text_{agent_id}")
    assert not fe.shown(f"run_pr_{agent_id}"), "there is no PR to open yet"

    pr_url = "https://github.com/acme/widgets/pull/7"
    persistent_client.hset(
        wire.cloudrun_key(agent_id),
        mapping={"status": wire.STATUS_FINISHED, "pr_url": pr_url},
    )

    harness.wait_until(
        lambda: fe.shown(f"run_pr_{agent_id}"), message="the PR button to appear"
    )
    assert wire.STATUS_FINISHED in fe.get(f"run_text_{agent_id}")

    fe.click(f"run_pr_{agent_id}")
    assert opened_urls == [pr_url]


@pytest.mark.canvas
@pytest.mark.git
def test_a_draft_spoken_into_voicedeck_reaches_a_cloud_agent(
    harness,
    voice_session,
    fake_realtime,
    cloud_dispatcher,
    fake_cloud_runtime,
    redis_client,
    persistent_client,
    read_stream,
    git_floor,
) -> None:
    """The whole voice path, with only the model and the VM faked.

    Three nodes have to agree on one hash for this to work, which is the reason
    the draft's fields are exactly CLOUDORDER's rather than a shape of their own.
    """
    from megadesk_contracts.wire import code_scope as scope_wire
    from VoiceDeckManager.tools import TOOL_DISPATCH_DOC_AGENT

    persistent_client.hset(
        scope_wire.session_key(scope_wire.new_session_id()),
        mapping=scope_wire.session_fields(
            repo="widgets", clone_path=str(git_floor.dev_dir)
        ),
    )
    fe = harness.drop("cloud_dispatcher")
    voice_session.start()

    fake_realtime.call_tool(
        TOOL_DISPATCH_DOC_AGENT, {"title": TITLE, "instructions": INSTRUCTIONS}
    )
    voice_session.pump_events()

    harness.wait_until(
        lambda: bool(fe.suffixes(r"^draft_go_")),
        message="the spoken draft to reach the dispatcher",
    )
    assert read_stream(wire.CLOUDORDER_STREAM) == [], "voice alone opens nothing"

    (suffix,) = fe.suffixes(r"^draft_go_")
    fe.click(suffix)
    assert cloud_dispatcher.poll_orders() == 1

    launch = fake_cloud_runtime.launches[0]
    assert launch["title"] == TITLE
    assert INSTRUCTIONS in launch["instructions"]
    # The URL came off the clone on disk, since nobody said it out loud.
    assert launch["repo_url"].endswith("origin.git")
    assert len(runs_on(persistent_client)) == 1


@pytest.mark.canvas
def test_a_run_that_never_started_says_what_to_check(
    harness, redis_client, read_stream
) -> None:
    fe = harness.drop("cloud_dispatcher")
    redis_client.xadd(
        wire.CLOUDFINISHED_STREAM,
        wire.cloudfinished_fields(
            order_id=wire.new_order_id(), status=wire.STATUS_STARTUP_ERROR
        ),
    )

    harness.wait_until(
        lambda: "CURSOR_API_KEY" in fe.get("status_text"),
        message="the FE to name the likely cause",
    )
