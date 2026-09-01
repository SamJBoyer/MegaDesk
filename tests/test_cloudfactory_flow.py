"""CloudFactory end to end, without spending money or opening a real PR.

``FakeCloudFactory`` stands in for Cursor's VM; everything either side of it is
real — the CLOUDORDER pub/sub signal, the reference stream, the run registry on
db 1, failure CLOUDFINISHED payloads, and the canvas widgets that list queued
orders and live agents.

The assertions cluster around one risk. Every other failure here is cosmetic, but
launching twice for one order means two pull requests, so duplicate suppression
and the retry rules get tested harder than the rest.
"""

from __future__ import annotations

import pytest
from conftest import (
    CLOUDFINISHED_CANONICAL_FIELDS,
    CLOUDORDER_CANONICAL_FIELDS,
    CLOUDRUN_CANONICAL_FIELDS,
)
from megadesk_contracts import AgentStartupError
from megadesk_contracts.wire import cloud as wire
from megadesk_contracts.wire.signal import publish_fields

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
    wire.publish_cloudorder(
        redis_client,
        wire.cloudorder_fields(
            order_id=order_id,
            repo_url=repo_url,
            title=title,
            instructions=instructions,
            auto_pr=auto_pr,
        ),
    )
    return order_id


def store_order(
    redis_client,
    *,
    order_id: str = "",
    repo_url: str = REPO_URL,
    title: str = TITLE,
    instructions: str = INSTRUCTIONS,
    auto_pr: bool = True,
) -> str:
    """Seed the CLOUDORDER stream without signalling execution."""
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


def pending_orders(cloud_factory) -> int:
    return cloud_factory.pending_count


def queue_labels(fe) -> list[str]:
    """Queue row labels. The queue is a list of lamp+selectable rows, not a listbox."""
    labels = fe.user_data("queue_list")
    if isinstance(labels, list):
        return [str(item) for item in labels]
    return [fe.label(suffix) for suffix in fe.suffixes(r"^queue_item_")]


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
    assert pending_orders(cloud_factory) == 0, "a handled order must not stay pending"


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


def test_an_unusable_order_is_stored_rather_than_retried_forever(
    cloud_factory, fake_cloud_factory, redis_client
) -> None:
    publish_fields(
        redis_client, wire.CLOUDORDER_CHANNEL, {"order_id": "", "repo_url": ""}
    )

    assert cloud_factory.poll_orders() == 0
    assert fake_cloud_factory.launches == []
    assert pending_orders(cloud_factory) == 0
    assert redis_client.xlen(wire.CLOUDORDER_STREAM) == 1


def test_a_resubscribe_still_receives_new_orders(
    cloud_factory, fake_cloud_factory, redis_client
) -> None:
    cloud_factory.ensure_listen()
    assert cloud_factory._inbox is not None
    cloud_factory._inbox.close()
    cloud_factory._inbox = None
    cloud_factory.ensure_listen()
    place_order(redis_client)

    assert cloud_factory.poll_orders() == 1
    assert len(fake_cloud_factory.launches) == 1


def test_a_stale_stream_entry_does_not_launch_an_agent(
    cloud_factory, fake_cloud_factory, redis_client
) -> None:
    store_order(redis_client)

    assert cloud_factory.poll_orders() == 0
    assert fake_cloud_factory.launches == []


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
    assert pending_orders(cloud_factory) == 0


def test_a_retryable_failure_is_retried_and_still_launches_only_once(
    cloud_factory, fake_cloud_factory, redis_client, persistent_client, read_stream
) -> None:
    """Cursor's own advice decides this; a blind retry could double-launch."""
    fake_cloud_factory.startup_error = "rate limited"
    fake_cloud_factory.retryable = True
    place_order(redis_client)

    assert cloud_factory.poll_orders() == 0
    assert finished(read_stream) == [], "a retryable failure is not an outcome yet"
    assert pending_orders(cloud_factory) == 1, "the order must stay claimed to be retried"

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
    assert pending_orders(cloud_factory) == 0


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
    """A PR URL hands the run to GitHub: cancel the VM, do not declare finished."""
    place_order(redis_client)
    cloud_factory.poll_orders()
    agent_id = fake_cloud_factory.launches[0]["agent_id"]

    # Still working: the registry updates, but nothing is announced.
    assert cloud_factory.poll_runs() == 0
    assert runs_on(persistent_client)[agent_id]["status"] == wire.STATUS_RUNNING
    assert finished(read_stream) == []

    cloud_factory.poll_runs()

    assert fake_cloud_factory.cancelled == [agent_id]
    stored = runs_on(persistent_client)[agent_id]
    assert stored["status"] == wire.STATUS_RUNNING
    assert "/pull/" in stored["pr_url"]
    assert finished(read_stream) == [], "handoff must not publish success CLOUDFINISHED"

    # Handed off: not polled again, even though the hash is still running.
    assert cloud_factory.poll_runs() == 0
    assert len(finished(read_stream)) == 0
    assert fake_cloud_factory.cancelled == [agent_id]


def test_only_unfinished_runs_are_polled(
    cloud_factory, fake_cloud_factory, persistent_client
) -> None:
    """A handed-off or failed run asked about again is a rate limit spent on old news."""
    seed_run(persistent_client, agent_id="bc-done01", status=wire.STATUS_ERROR)
    seed_run(
        persistent_client,
        agent_id="bc-pr01",
        pr_url="https://github.com/acme/widgets/pull/1",
    )
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


def test_rejecting_an_unlaunched_order_cancels_it_without_starting_an_agent(
    cloud_factory, fake_cloud_factory, redis_client, persistent_client, read_stream
) -> None:
    order_id = place_order(redis_client)

    assert cloud_factory.reject(order_id) is True
    assert cloud_factory.poll_orders() == 0

    assert fake_cloud_factory.launches == []
    assert runs_on(persistent_client) == {}
    reports = finished(read_stream)
    assert len(reports) == 1
    assert reports[0]["status"] == wire.STATUS_CANCELLED
    assert reports[0]["order_id"] == order_id
    assert reports[0]["agent_id"] == ""
    assert pending_orders(cloud_factory) == 0


def test_rejecting_a_live_run_cancels_the_agent(
    cloud_factory, fake_cloud_factory, redis_client, persistent_client, read_stream
) -> None:
    order_id = place_order(redis_client)
    cloud_factory.poll_orders()
    agent_id = fake_cloud_factory.launches[0]["agent_id"]

    assert cloud_factory.reject(order_id) is True
    assert fake_cloud_factory.cancelled == [agent_id]
    assert runs_on(persistent_client)[agent_id]["status"] == wire.STATUS_CANCELLED
    assert finished(read_stream)[-1]["status"] == wire.STATUS_CANCELLED


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

    async def fake_launch(*, model, cloud, instructions, title, **_kw):
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
    on it holds a pull request, so polling the agent reports ``running`` with
    no PR forever: the manager never hands off and the VM keeps writing. Nothing
    raises, which is why this needs a test rather than a log.
    ``runtime="cloud"`` is part of the contract too — without it the SDK checks
    the local run store and raises ``AgentNotFoundError``.

    SDK ``finished`` / ``completed`` / ``success`` is not MegaDesk finished.
    A PR URL is only a kill switch for the VM; CLOUDFINISHED is not the
    success path.
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
    assert state.status == wire.STATUS_RUNNING
    assert state.status != wire.STATUS_FINISHED
    assert state.result == pr_url, "the PR lives at run.git.branches[*].pr_url"
    assert asked == [("bc-abc123", "cloud")]

    still_running = poll_against(
        [_run("running", created_at="01", branches=(branch,))]
    )
    assert still_running.status == wire.STATUS_RUNNING
    assert still_running.result == pr_url

    gone = poll_against([_run("finished", created_at="01")])
    assert gone.status == wire.STATUS_ERROR
    assert gone.result == ""

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


def test_a_branch_verification_error_names_the_github_connection() -> None:
    """Cursor blames the ref; the log has to say it is the GitHub app."""
    from CloudFactoryManager.runtime import CursorCloudFactory

    class Refusal(Exception):
        message = "[validation_error] Failed to verify existence of branch 'dev'"

    error = CursorCloudFactory._startup_error(Refusal(), "cloud agent could not be created")
    assert isinstance(error, AgentStartupError)
    assert "Failed to verify existence of branch" in str(error)
    assert "GitHub app" in str(error)
    assert "CURSOR_API_KEY" in str(error)


def test_listed_repo_urls_read_the_shapes_the_sdk_actually_returns() -> None:
    from types import SimpleNamespace

    from CloudFactoryManager.runtime import listed_repo_urls, repo_is_connected

    assert listed_repo_urls(
        [SimpleNamespace(url="https://github.com/acme/widgets.git")]
    ) == ["https://github.com/acme/widgets.git"]
    assert listed_repo_urls(
        SimpleNamespace(repositories=[{"url": "https://github.com/acme/widgets"}])
    ) == ["https://github.com/acme/widgets"]
    assert listed_repo_urls("https://github.com/acme/widgets") == [
        "https://github.com/acme/widgets"
    ]
    assert repo_is_connected(
        "https://github.com/acme/widgets",
        ["https://github.com/acme/widgets.git"],
    )
    assert not repo_is_connected(
        "https://github.com/acme/widgets",
        ["https://github.com/acme/other"],
    )


def test_launch_refuses_when_cursor_cannot_see_the_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Starting the VM first is what used to log a missing branch for 15s."""
    import asyncio
    from types import SimpleNamespace

    from CloudFactoryManager.runtime import CursorCloudFactory

    created: list[object] = []

    class _Agents:
        async def create(self, **kwargs):
            created.append(kwargs)
            raise AssertionError("must not create an agent for an unconnected repo")

    class _Client:
        agents = _Agents()

        async def list_repositories(self):
            return []

    runtime = CursorCloudFactory(api_key="key")

    async def fake_client():
        return _Client()

    monkeypatch.setattr(runtime, "_ensure_client", fake_client)
    monkeypatch.setattr(runtime, "_run", lambda coro: asyncio.run(coro))
    monkeypatch.setattr(runtime, "_options_cls", lambda: SimpleNamespace)

    with pytest.raises(AgentStartupError, match="empty repository list") as caught:
        runtime.launch(
            {
                "repo_url": REPO_URL,
                "instructions": INSTRUCTIONS,
                "title": TITLE,
            }
        )
    assert "GitHub app" in str(caught.value)
    assert "missing-branch" in str(caught.value)
    assert created == []


def test_launch_refuses_when_the_repo_is_missing_from_cursors_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    from types import SimpleNamespace

    from CloudFactoryManager.runtime import CursorCloudFactory

    created: list[object] = []

    class _Agents:
        async def create(self, **kwargs):
            created.append(kwargs)
            raise AssertionError("must not create an agent for an unconnected repo")

    class _Repos:
        async def list(self, api_key=None):
            return [SimpleNamespace(url="https://github.com/acme/other")]

    class _Client:
        agents = _Agents()
        repositories = _Repos()

    runtime = CursorCloudFactory(api_key="key")

    async def fake_client():
        return _Client()

    monkeypatch.setattr(runtime, "_ensure_client", fake_client)
    monkeypatch.setattr(runtime, "_run", lambda coro: asyncio.run(coro))
    monkeypatch.setattr(runtime, "_options_cls", lambda: SimpleNamespace)

    with pytest.raises(AgentStartupError, match="not connected to Cursor") as caught:
        runtime.launch(
            {
                "repo_url": REPO_URL,
                "instructions": INSTRUCTIONS,
                "title": TITLE,
            }
        )
    assert "missing-branch" in str(caught.value)
    assert created == []


def test_launch_proceeds_when_the_repo_is_on_cursors_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    from types import SimpleNamespace

    from CloudFactoryManager.runtime import CursorCloudFactory
    from megadesk_contracts import RunHandle

    created: list[object] = []

    class _Agent:
        agent_id = "bc-listed01"

        async def send(self, _prompt):
            return SimpleNamespace(id="run-1")

    class _Agents:
        async def create(self, **kwargs):
            created.append(kwargs)
            return _Agent()

    class _Repos:
        async def list(self, api_key=None):
            return [SimpleNamespace(url=REPO_URL)]

    class _Client:
        agents = _Agents()
        repositories = _Repos()

    runtime = CursorCloudFactory(api_key="key")

    async def fake_client():
        return _Client()

    monkeypatch.setattr(runtime, "_ensure_client", fake_client)
    monkeypatch.setattr(runtime, "_run", lambda coro: asyncio.run(coro))
    monkeypatch.setattr(runtime, "_options_cls", lambda: SimpleNamespace)

    handle = runtime.launch(
        {
            "repo_url": REPO_URL + ".git",
            "instructions": INSTRUCTIONS,
            "title": TITLE,
        }
    )
    assert handle == RunHandle(run_key="bc-listed01", run_id="run-1")
    assert len(created) == 1


def test_launch_attaches_order_pictures_to_the_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    from types import SimpleNamespace

    from CloudFactoryManager.runtime import CursorCloudFactory
    from megadesk_contracts import RunHandle

    sent: list[object] = []
    shot = "https://github.com/user-attachments/assets/cloud-shot"

    class _Agent:
        agent_id = "bc-pics01"

        async def send(self, payload):
            sent.append(payload)
            return SimpleNamespace(id="run-1")

    class _Agents:
        async def create(self, **kwargs):
            return _Agent()

    class _Repos:
        async def list(self, api_key=None):
            return [SimpleNamespace(url=REPO_URL)]

    class _Client:
        agents = _Agents()
        repositories = _Repos()

    runtime = CursorCloudFactory(api_key="key")

    async def fake_client():
        return _Client()

    monkeypatch.setattr(runtime, "_ensure_client", fake_client)
    monkeypatch.setattr(runtime, "_run", lambda coro: asyncio.run(coro))
    monkeypatch.setattr(runtime, "_options_cls", lambda: SimpleNamespace)

    handle = runtime.launch(
        {
            "repo_url": REPO_URL,
            "instructions": INSTRUCTIONS,
            "title": TITLE,
            "pictures": [shot],
        }
    )
    assert handle == RunHandle(run_key="bc-pics01", run_id="run-1")
    assert sent, "agent.send was never called"
    payload = sent[0]
    if isinstance(payload, dict):
        text = payload.get("text", "")
        images = payload.get("images") or []
    else:
        text = getattr(payload, "text", "")
        images = getattr(payload, "images", None) or []
    assert "Reference images are attached" in str(text)
    assert any(shot in str(item) for item in images)


# --- the frontend ----------------------------------------------------------


def test_each_order_gets_its_own_lamp_state() -> None:
    """One shared lamp would paint every ticket with the worst status in the queue."""
    from cloud_factory_frontend.app import (
        LAMP_ERROR,
        LAMP_OK,
        LAMP_STARTING,
        lamp_for_order,
    )

    assert lamp_for_order(status="", order_id="boot") == LAMP_STARTING
    assert lamp_for_order(status=wire.STATUS_STARTUP_ERROR, order_id="dead") is LAMP_ERROR
    assert lamp_for_order(status=wire.STATUS_ERROR, order_id="dead") is LAMP_ERROR
    assert lamp_for_order(status=wire.STATUS_RUNNING, order_id="live") is LAMP_OK
    assert (
        lamp_for_order(
            status=wire.STATUS_RUNNING,
            pr_url="https://github.com/acme/widgets/pull/1",
            order_id="done",
        )
        is LAMP_OK
    )
    assert lamp_for_order(status=wire.STATUS_CANCELLED, order_id="nope") is LAMP_OK


@pytest.mark.canvas
def test_work_dispatcher_publishes_a_canonical_cloudorder(
    harness, redis_client, fake_gh, cloudorders, cloud_factory, fake_cloud_factory
) -> None:
    fake_gh.add_issue(41, TITLE, INSTRUCTIONS)
    dispatcher = harness.drop("work_dispatcher")
    dispatcher.type_into("git_url", REPO_URL)
    harness.wait_for_widget(dispatcher, "ticket_btn_41")
    dispatcher.select("ticket_factory_41", "cloud")
    dispatcher.click("ticket_btn_41")

    orders = cloudorders()
    assert len(orders) == 1
    _entry_id, order = orders[0]
    assert set(order) == set(CLOUDORDER_CANONICAL_FIELDS)
    assert order["repo_url"] == REPO_URL
    assert order["instructions"] == INSTRUCTIONS
    assert order["title"] == TITLE
    assert order["auto_pr"] == "true"

    assert cloud_factory.poll_orders() == 1
    fe = harness.drop("cloud_factory")
    harness.wait_until(
        lambda: any(TITLE in item for item in queue_labels(fe)),
        message="the processed order to reach the CloudFactory queue",
    )


@pytest.mark.canvas
def test_processed_orders_and_live_agents_share_the_machine_factory_layout(
    harness, redis_client, persistent_client
) -> None:
    order_id = store_order(redis_client)
    seed_run(persistent_client)
    fe = harness.drop("cloud_factory")

    harness.wait_until(
        lambda: any(TITLE in item for item in queue_labels(fe)),
        message="the processed order to appear",
    )
    harness.wait_until(
        lambda: any(wire.STATUS_RUNNING in item for item in fe.items("live_list")),
        message="the live agent to appear",
    )
    assert fe.exists("queue_list")
    assert fe.exists("live_list")
    assert fe.exists(f"queue_lamp_{order_id}")
    assert not fe.exists("error_lamp")
    assert fe.exists("reject_btn")
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
    order_id = store_order(redis_client)
    agent_id = seed_run(persistent_client, order_id=order_id)
    fe = harness.drop("cloud_factory")

    harness.wait_until(
        lambda: any(wire.STATUS_RUNNING in item for item in fe.items("live_list")),
        message="the live agent to appear",
    )

    pr_url = "https://github.com/acme/widgets/pull/7"
    persistent_client.hset(
        wire.cloudrun_key(agent_id),
        mapping={"status": wire.STATUS_RUNNING, "pr_url": pr_url},
    )

    harness.wait_until(
        lambda: fe.items("live_list") == ["(no live agents)"],
        message="the live list to clear after handoff",
    )
    assert all(wire.STATUS_FINISHED not in item for item in queue_labels(fe))


@pytest.mark.canvas
def test_a_spoken_order_reaches_a_cloud_agent(
    harness,
    voice_session,
    fake_realtime,
    fake_codescope,
    cloud_factory,
    fake_cloud_factory,
    redis_client,
    persistent_client,
    read_stream,
) -> None:
    """The whole voice path, with only the model and the VM faked."""
    from VoiceDeckManager.tools import TOOL_DISPATCH_DOC_AGENT

    fake_codescope.seed_repo(
        repo="widgets", url="https://github.com/acme/widgets.git"
    )
    fe = harness.drop("cloud_factory")
    voice_session.start()

    fake_realtime.call_tool(
        TOOL_DISPATCH_DOC_AGENT, {"title": TITLE, "instructions": INSTRUCTIONS}
    )
    voice_session.pump_events()

    assert cloud_factory.poll_orders() == 1
    harness.wait_until(
        lambda: any(TITLE in item for item in queue_labels(fe)),
        message="the spoken order to reach the CloudFactory queue",
    )

    launch = fake_cloud_factory.launches[0]
    assert launch["title"] == TITLE
    assert INSTRUCTIONS in launch["instructions"]
    assert launch["repo_url"] == "https://github.com/acme/widgets.git"
    assert len(runs_on(persistent_client)) == 1


@pytest.mark.canvas
def test_a_run_that_never_started_turns_that_orders_lamp_red(
    harness, redis_client
) -> None:
    order_id = store_order(redis_client)
    redis_client.xadd(
        wire.CLOUDFINISHED_STREAM,
        wire.cloudfinished_fields(
            order_id=order_id, status=wire.STATUS_STARTUP_ERROR
        ),
    )
    fe = harness.drop("cloud_factory")

    harness.wait_until(
        lambda: fe.exists(f"queue_lamp_{order_id}")
        and fe.user_data(f"queue_lamp_{order_id}") is True,
        message="that order's lamp to turn red",
    )


@pytest.mark.canvas
def test_a_queued_order_blinks_its_own_lamp_while_no_agent_exists(
    harness, redis_client
) -> None:
    order_id = store_order(redis_client)
    fe = harness.drop("cloud_factory")

    harness.wait_until(
        lambda: any(TITLE in item for item in queue_labels(fe)),
        message="the queued order to appear",
    )
    harness.wait_until(
        lambda: fe.user_data(f"queue_lamp_{order_id}") == "starting",
        message="that order's lamp to blink starting while the agent is still booting",
    )
    assert all("running" not in item for item in fe.items("live_list"))


@pytest.mark.canvas
def test_queued_orders_do_not_share_one_lamp(
    harness, redis_client, persistent_client
) -> None:
    booting = store_order(redis_client, title="Booting ticket")
    dead = store_order(redis_client, title="Dead ticket")
    live = store_order(redis_client, title="Live ticket")
    redis_client.xadd(
        wire.CLOUDFINISHED_STREAM,
        wire.cloudfinished_fields(
            order_id=dead, status=wire.STATUS_STARTUP_ERROR
        ),
    )
    seed_run(persistent_client, order_id=live, status=wire.STATUS_RUNNING)
    fe = harness.drop("cloud_factory")

    harness.wait_until(
        lambda: fe.exists(f"queue_lamp_{booting}")
        and fe.exists(f"queue_lamp_{dead}")
        and fe.exists(f"queue_lamp_{live}"),
        message="each queued order to grow its own lamp",
    )
    assert fe.user_data(f"queue_lamp_{booting}") == "starting"
    assert fe.user_data(f"queue_lamp_{dead}") is True
    assert fe.user_data(f"queue_lamp_{live}") is False


@pytest.mark.canvas
def test_reject_refuses_a_queued_order_before_it_launches(
    harness, redis_client, read_stream
) -> None:
    order_id = store_order(redis_client)
    fe = harness.drop("cloud_factory")

    harness.wait_until(
        lambda: fe.exists(f"queue_item_{order_id}"),
        message="the queued order to appear",
    )
    fe.click(f"queue_item_{order_id}")
    fe.click("reject_btn")

    harness.wait_until(
        lambda: any(wire.STATUS_CANCELLED in item for item in queue_labels(fe)),
        message="the rejected order to show cancelled",
    )
    reports = [fields for _id, fields in read_stream(wire.CLOUDFINISHED_STREAM)]
    assert reports[-1]["status"] == wire.STATUS_CANCELLED
    assert reports[-1]["agent_id"] == ""
    assert fe.user_data(f"queue_lamp_{order_id}") is False
