"""CloudFactory end to end, without spending money or opening a real PR.

``FakeCloudFactory`` stands in for Cursor's VM; everything either side of it is
real — the CLOUDORDER consumer group, the run registry on db 1, the CLOUDFINISHED
payloads, and the canvas widgets that list queued orders and live agents.

The assertions cluster around one risk. Every other failure here is cosmetic, but
launching twice for one order means two pull requests, so duplicate suppression
and the retry rules get tested harder than the rest.
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


def seed_run(
    persistent_client,
    *,
    agent_id: str = "bc-seeded01",
    status: str = wire.STATUS_RUNNING,
    pr_url: str = "",
    title: str = TITLE,
    order_id: str = "",
) -> str:
    persistent_client.hset(
        wire.cloudrun_key(agent_id),
        mapping=wire.cloudrun_fields(
            order_id=order_id or wire.new_order_id(),
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
    cloud_factory, fake_cloud_factory, redis_client, persistent_client, read_stream
) -> None:
    order_id = place_order(redis_client)

    assert cloud_factory.poll_orders() == 1

    assert len(fake_cloud_factory.launches) == 1
    launch = fake_cloud_factory.launches[0]
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
    cloud_factory, fake_cloud_factory, redis_client, persistent_client
) -> None:
    """Two launches for one order means two pull requests for one request."""
    order_id = place_order(redis_client)
    cloud_factory.poll_orders()
    place_order(redis_client, order_id=order_id)
    cloud_factory.poll_orders()

    assert len(fake_cloud_factory.launches) == 1
    assert len(runs_on(persistent_client)) == 1


def test_an_unusable_order_is_acked_rather_than_retried_forever(
    cloud_factory, fake_cloud_factory, redis_client
) -> None:
    redis_client.xadd(wire.CLOUDORDER_STREAM, {"order_id": "", "repo_url": ""})

    assert cloud_factory.poll_orders() == 0
    assert fake_cloud_factory.launches == []
    assert pending_orders(redis_client) == 0


def test_a_flushed_consumer_group_is_recreated(
    cloud_factory, fake_cloud_factory, redis_client
) -> None:
    """A Redis flush after startup used to leave the BE logging NOGROUP forever."""
    cloud_factory.ensure_group()
    redis_client.delete(wire.CLOUDORDER_STREAM)
    place_order(redis_client)

    assert cloud_factory.poll_orders() == 1
    assert len(fake_cloud_factory.launches) == 1


# --- the two failure modes -------------------------------------------------


def test_a_launch_that_never_started_is_reported_with_no_agent(
    cloud_factory, fake_cloud_factory, redis_client, persistent_client, read_stream
) -> None:
    """No agent id exists, so the report cannot carry one — and must not invent one."""
    fake_cloud_factory.startup_error = "no CURSOR_API_KEY in the environment"
    order_id = place_order(redis_client)

    assert cloud_factory.poll_orders() == 0

    reports = finished(read_stream)
    assert len(reports) == 1
    assert set(reports[0]) == set(CLOUDFINISHED_CANONICAL_FIELDS)
    assert reports[0]["status"] == wire.STATUS_STARTUP_ERROR
    assert reports[0]["order_id"] == order_id
    assert reports[0]["agent_id"] == ""
    assert runs_on(persistent_client) == {}
    assert pending_orders(redis_client) == 0


def test_a_retryable_failure_is_retried_and_still_launches_only_once(
    cloud_factory, fake_cloud_factory, redis_client, persistent_client, read_stream
) -> None:
    """Cursor's own advice decides this; a blind retry could double-launch."""
    fake_cloud_factory.startup_error = "rate limited"
    fake_cloud_factory.retryable = True
    place_order(redis_client)

    assert cloud_factory.poll_orders() == 0
    assert finished(read_stream) == [], "a retryable failure is not an outcome yet"
    assert pending_orders(redis_client) == 1, "the order must stay claimed to be retried"

    fake_cloud_factory.startup_error = ""
    assert cloud_factory.poll_orders() == 1

    assert len(fake_cloud_factory.launches) == 1
    assert len(runs_on(persistent_client)) == 1


def test_a_retryable_failure_gives_up_and_reports(
    cloud_factory, fake_cloud_factory, redis_client, read_stream
) -> None:
    fake_cloud_factory.startup_error = "rate limited"
    fake_cloud_factory.retryable = True
    place_order(redis_client)

    for _attempt in range(4):
        cloud_factory.poll_orders()

    reports = finished(read_stream)
    assert len(reports) == 1, "one order produces one outcome, however many attempts"
    assert reports[0]["status"] == wire.STATUS_STARTUP_ERROR
    assert pending_orders(redis_client) == 0


def test_a_run_that_ran_and_failed_is_not_a_startup_error(
    cloud_factory, fake_cloud_factory, redis_client, persistent_client, read_stream
) -> None:
    """Different fix, different retry advice: the transcript is what to look at."""
    fake_cloud_factory.run_error = "the agent could not find the file"
    fake_cloud_factory.polls_before_finish = 0
    place_order(redis_client)
    cloud_factory.poll_orders()

    assert cloud_factory.poll_runs() == 1

    reports = finished(read_stream)
    assert len(reports) == 1
    assert reports[0]["status"] == wire.STATUS_ERROR
    assert reports[0]["agent_id"].startswith(wire.CLOUD_AGENT_ID_PREFIX)
    assert reports[0]["pr_url"] == ""
    agent_id = reports[0]["agent_id"]
    assert runs_on(persistent_client)[agent_id]["status"] == wire.STATUS_ERROR


# --- following a run to its pull request -----------------------------------


def test_a_finished_run_is_reported_once_with_its_pr_link(
    cloud_factory, fake_cloud_factory, redis_client, persistent_client, read_stream
) -> None:
    place_order(redis_client)
    cloud_factory.poll_orders()
    agent_id = fake_cloud_factory.launches[0]["agent_id"]

    # Still working: the registry updates, but nothing is announced.
    assert cloud_factory.poll_runs() == 0
    assert runs_on(persistent_client)[agent_id]["status"] == wire.STATUS_RUNNING
    assert finished(read_stream) == []

    assert cloud_factory.poll_runs() == 1

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
    assert cloud_factory.poll_runs() == 0
    assert len(finished(read_stream)) == 1


def test_only_unfinished_runs_are_polled(
    cloud_factory, fake_cloud_factory, persistent_client
) -> None:
    """A finished run asked about again is a rate limit spent on old news."""
    seed_run(persistent_client, agent_id="bc-done01", status=wire.STATUS_FINISHED)
    live = seed_run(persistent_client, agent_id="bc-live01")

    assert [agent_id for agent_id, _run in cloud_factory.live_runs()] == [live]


def test_cancelling_a_run_stops_it_and_says_so(
    cloud_factory, fake_cloud_factory, redis_client, persistent_client, read_stream
) -> None:
    place_order(redis_client)
    cloud_factory.poll_orders()
    agent_id = fake_cloud_factory.launches[0]["agent_id"]

    assert cloud_factory.cancel(agent_id) is True

    assert fake_cloud_factory.cancelled == [agent_id]
    assert runs_on(persistent_client)[agent_id]["status"] == wire.STATUS_CANCELLED
    assert finished(read_stream)[-1]["status"] == wire.STATUS_CANCELLED
    assert cloud_factory.poll_runs() == 0, "a cancelled run is already accounted for"


# --- the real runtime's cloud options --------------------------------------


def test_the_cloud_runtime_asks_for_a_pr_and_never_runs_locally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SDK defaults to local when neither runtime is set, which would be silent.

    Cut above ``cursor_sdk`` rather than at the network, because the bug this
    guards against is a missing keyword argument, not a bad response. The
    production path is async (Windows cannot ``select()`` a pipe); this test
    still inspects the ``cloud=`` options the runtime would pass. Empty ``ref``
    sends ``startingRef=dev``, the branch factories start work from.
    """
    import asyncio

    from CloudFactoryManager.runtime import CursorCloudFactory, prompt_for
    from megadesk_contracts import RunHandle

    created: dict = {}
    prompts: list[str] = []

    class _StubOptions:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

    async def fake_launch(*, model, cloud, instructions, title):
        created["model"] = model
        created["cloud"] = cloud
        prompts.append(prompt_for(instructions=instructions, title=title))
        return RunHandle(run_key="bc-stub001", run_id="run-1")

    runtime = CursorCloudFactory(api_key="key")
    monkeypatch.setattr(runtime, "_options_cls", lambda: _StubOptions)
    monkeypatch.setattr(runtime, "_async_launch", fake_launch)
    monkeypatch.setattr(runtime, "_run", lambda coro: asyncio.run(coro))

    handle = runtime.launch(
        {
            "repo_url": REPO_URL,
            "instructions": INSTRUCTIONS,
            "title": TITLE,
            "model": "auto",
        }
    )

    assert handle.run_key == "bc-stub001"
    assert handle.run_id == "run-1"
    options = created["cloud"].kwargs
    assert options["repos"] == [{"url": REPO_URL, "startingRef": "dev"}]
    assert options["auto_create_pr"] is True
    assert options["skip_reviewer_request"] is True
    assert "ref" not in options
    assert INSTRUCTIONS in prompts[0]
    assert TITLE in prompts[0]

    runtime.launch(
        {
            "repo_url": REPO_URL,
            "instructions": INSTRUCTIONS,
            "title": TITLE,
            "model": "auto",
            "ref": "main",
        }
    )
    assert created["cloud"].kwargs["repos"] == [
        {"url": REPO_URL, "startingRef": "main"}
    ]


def test_the_smoke_repo_is_identified_by_name_not_owner_slash_name() -> None:
    """Cursor prints nameWithOwner; MegaDesk's name is the last path segment."""
    from CloudFactoryManager.runtime import canonical_github_repo, cloud_launch_options
    from work_dispatcher_app import normalize_repo_url, parse_github_repo

    git_url = "https://github.com/SamJBoyer/SMOKETESTREPO.git"
    owner, repo = parse_github_repo(git_url)
    assert repo == "SMOKETESTREPO"
    assert owner == "SamJBoyer"
    assert normalize_repo_url(git_url, owner, repo) == (
        "https://github.com/SamJBoyer/SMOKETESTREPO"
    )

    url, name = canonical_github_repo(git_url)
    assert name == "SMOKETESTREPO"
    assert "/" not in name
    assert url == "https://github.com/SamJBoyer/SMOKETESTREPO"

    options = cloud_launch_options(repo_url=git_url)
    assert options["repos"][0]["url"] == url
    assert options["repos"][0]["url"].rsplit("/", 1)[-1] == "SMOKETESTREPO"

    slug_url, slug_name = canonical_github_repo("SamJBoyer/SMOKETESTREPO")
    assert slug_name == "SMOKETESTREPO"
    assert slug_url == url


def test_cloud_agent_options_serialize_repo_urls_as_mappings() -> None:
    """A bare URL in ``repos`` is what production logged as a dict() failure."""
    pytest.importorskip("cursor_sdk")
    from cursor_sdk import CloudAgentOptions
    from CloudFactoryManager.runtime import cloud_launch_options

    payload = CloudAgentOptions(**cloud_launch_options(repo_url=REPO_URL)).to_json()
    assert payload["repos"] == [{"url": REPO_URL, "startingRef": "dev"}]
    assert payload["autoCreatePr"] is True
    assert payload["skipReviewerRequest"] is True

    with_ref = CloudAgentOptions(
        **cloud_launch_options(repo_url=REPO_URL, ref="main")
    ).to_json()
    assert with_ref["repos"] == [{"url": REPO_URL, "startingRef": "main"}]


def test_poll_asks_the_run_because_a_cloud_agent_carries_no_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``agents.get`` cannot answer this question, and fails silently when asked.

    For a cloud agent ``SDKAgentInfo.status`` is ``None`` and no field anywhere
    on it holds a pull request, so polling the agent reports ``running`` for a
    run that finished minutes ago: CLOUDFINISHED never fires and the FE waits
    forever. Nothing raises, which is why this needs a test rather than a log.
    ``runtime="cloud"`` is part of the contract too — without it the SDK checks
    the local run store and raises ``AgentNotFoundError``.
    """
    import asyncio
    from types import SimpleNamespace

    from CloudFactoryManager.runtime import CursorCloudFactory

    pr_url = "https://github.com/acme/widgets/pull/3"
    asked: list[tuple[str, str | None]] = []

    class _Agents:
        async def get(self, agent_id, api_key=None):
            raise AssertionError("poll must not ask the agent for run state")

    class _Client:
        agents = _Agents()

        def __init__(self, runs) -> None:
            self._runs = runs

        async def list_runs(self, agent_id, *, runtime=None, api_key=None):
            asked.append((agent_id, runtime))
            return self._runs

    def _run(status: str, *, created_at: str, branches=()) -> SimpleNamespace:
        return SimpleNamespace(
            id="run-1",
            created_at=created_at,
            status=status,
            git=SimpleNamespace(branches=branches),
        )

    def poll_against(runs) -> object:
        runtime = CursorCloudFactory(api_key="key")
        client = _Client(runs)

        async def fake_client():
            return client

        monkeypatch.setattr(runtime, "_ensure_client", fake_client)
        monkeypatch.setattr(runtime, "_run", lambda coro: asyncio.run(coro))
        return runtime.poll("bc-abc123")

    branch = SimpleNamespace(
        repo_url="github.com/acme/widgets",
        branch="cursor/document-the-frame-pump-reset",
        pr_url=pr_url,
    )
    state = poll_against([_run("finished", created_at="01", branches=(branch,))])
    assert state.status == wire.STATUS_FINISHED
    assert state.result == pr_url, "the PR lives at run.git.branches[*].pr_url"
    assert asked == [("bc-abc123", "cloud")]

    # An agent Cursor has not opened a run for yet is still running, not finished.
    assert poll_against([]).status == wire.STATUS_RUNNING

    # Several runs on one agent: the newest is the one that says where it got to.
    newest = poll_against(
        [
            _run("finished", created_at="01", branches=(branch,)),
            _run("running", created_at="02"),
        ]
    )
    assert newest.status == wire.STATUS_RUNNING
    assert newest.result == ""


def test_an_unknown_status_is_treated_as_still_running() -> None:
    """Guessing 'finished' would close a run that is still writing to a branch."""
    from megadesk_contracts.wire.factory import normalize_status

    assert normalize_status("CREATING") == wire.STATUS_RUNNING
    assert normalize_status("something new") == wire.STATUS_RUNNING
    assert normalize_status("") == wire.STATUS_RUNNING
    assert normalize_status("FINISHED") == wire.STATUS_FINISHED
    assert normalize_status("failed") == wire.STATUS_ERROR


def test_a_launch_failure_keeps_cursors_retry_advice() -> None:
    """Dropping ``retryable`` would turn a rate limit into a lost order."""
    from CloudFactoryManager.runtime import CursorCloudFactory

    class Refusal(Exception):
        message = "rate limited"
        is_retryable = True
        retry_after = 12

    error = CursorCloudFactory._startup_error(Refusal(), "launch refused")
    assert isinstance(error, AgentStartupError)
    assert error.retryable is True
    assert error.retry_after == 12
    assert "rate limited" in str(error)


# --- the frontend ----------------------------------------------------------


@pytest.mark.canvas
def test_work_dispatcher_publishes_a_canonical_cloudorder(
    harness, redis_client, fake_gh, read_stream
) -> None:
    fake_gh.add_issue(41, TITLE, INSTRUCTIONS)
    dispatcher = harness.drop("work_dispatcher")
    dispatcher.type_into("git_url", REPO_URL)
    harness.wait_for_widget(dispatcher, "ticket_btn_41")
    dispatcher.select("ticket_factory_41", "cloud")
    dispatcher.click("ticket_btn_41")

    orders = read_stream(wire.CLOUDORDER_STREAM)
    assert len(orders) == 1
    _entry_id, order = orders[0]
    assert set(order) == set(CLOUDORDER_CANONICAL_FIELDS)
    assert order["repo_url"] == REPO_URL
    assert order["instructions"] == INSTRUCTIONS
    assert order["title"] == TITLE
    assert order["auto_pr"] == "true"

    fe = harness.drop("cloud_factory")
    harness.wait_until(
        lambda: any(TITLE in item for item in fe.items("queue_list")),
        message="the processed order to reach the CloudFactory queue",
    )


@pytest.mark.canvas
def test_processed_orders_and_live_agents_share_the_machine_factory_layout(
    harness, redis_client, persistent_client
) -> None:
    place_order(redis_client)
    seed_run(persistent_client)
    fe = harness.drop("cloud_factory")

    harness.wait_until(
        lambda: any(TITLE in item for item in fe.items("queue_list")),
        message="the processed order to appear",
    )
    harness.wait_until(
        lambda: any(wire.STATUS_RUNNING in item for item in fe.items("live_list")),
        message="the live agent to appear",
    )
    assert fe.exists("queue_list")
    assert fe.exists("live_list")
    assert fe.exists("error_lamp")
    assert not fe.exists("draft_list")
    assert not fe.exists("docker_list")
    assert not fe.exists("send_btn")
    assert not fe.exists("repo_url")
    assert not fe.exists("instructions")
    assert not fe.exists("git_url")
    assert not fe.exists("status_lbl")
    assert not fe.exists("redis_dot")
    assert not fe.exists("detail")
    assert not fe.exists("pr_btn")


@pytest.mark.canvas
def test_a_run_shows_its_status_in_the_queue(
    harness, redis_client, persistent_client
) -> None:
    order_id = place_order(redis_client)
    agent_id = seed_run(persistent_client, order_id=order_id)
    fe = harness.drop("cloud_factory")

    harness.wait_until(
        lambda: any(wire.STATUS_RUNNING in item for item in fe.items("live_list")),
        message="the live agent to appear",
    )

    pr_url = "https://github.com/acme/widgets/pull/7"
    persistent_client.hset(
        wire.cloudrun_key(agent_id),
        mapping={"status": wire.STATUS_FINISHED, "pr_url": pr_url},
    )

    harness.wait_until(
        lambda: any(wire.STATUS_FINISHED in item for item in fe.items("queue_list")),
        message="the processed order to show finished",
    )
    assert all(wire.STATUS_RUNNING not in item for item in fe.items("live_list"))


@pytest.mark.canvas
@pytest.mark.git
def test_a_spoken_order_reaches_a_cloud_agent(
    harness,
    voice_session,
    fake_realtime,
    cloud_factory,
    fake_cloud_factory,
    redis_client,
    persistent_client,
    read_stream,
    git_floor,
) -> None:
    """The whole voice path, with only the model and the VM faked."""
    from megadesk_contracts.wire import code_scope as scope_wire
    from VoiceDeckManager.tools import TOOL_DISPATCH_DOC_AGENT

    persistent_client.hset(
        scope_wire.session_key(scope_wire.new_session_id()),
        mapping=scope_wire.session_fields(
            repo="widgets", clone_path=str(git_floor.dev_dir)
        ),
    )
    fe = harness.drop("cloud_factory")
    voice_session.start()

    fake_realtime.call_tool(
        TOOL_DISPATCH_DOC_AGENT, {"title": TITLE, "instructions": INSTRUCTIONS}
    )
    voice_session.pump_events()

    harness.wait_until(
        lambda: any(TITLE in item for item in fe.items("queue_list")),
        message="the spoken order to reach the CloudFactory queue",
    )
    assert cloud_factory.poll_orders() == 1

    launch = fake_cloud_factory.launches[0]
    assert launch["title"] == TITLE
    assert INSTRUCTIONS in launch["instructions"]
    # The URL came off the clone on disk, since nobody said it out loud.
    assert launch["repo_url"].endswith("origin.git")
    assert len(runs_on(persistent_client)) == 1


@pytest.mark.canvas
def test_a_run_that_never_started_turns_the_lamp_red(
    harness, redis_client, read_stream
) -> None:
    fe = harness.drop("cloud_factory")
    redis_client.xadd(
        wire.CLOUDFINISHED_STREAM,
        wire.cloudfinished_fields(
            order_id=wire.new_order_id(), status=wire.STATUS_STARTUP_ERROR
        ),
    )

    harness.wait_until(
        lambda: fe.user_data("error_lamp") is True,
        message="the error lamp to turn red",
    )
