"""MachineFactory's order loop and its reaper, without a Docker daemon.

``FakeMachineFactory`` stands in for the sandbox; everything either side of it is
real — the WORKORDER pub/sub signal, the reference stream, the AGENTHANDLER
registry and the FINISHED payloads the factory publishes. MachineFactory clones
into the sandbox (no Floor worktrees) and hands back a pull-request URL.

The cloud suite's risk is launching twice and opening two pull requests. Here the
risk is the opposite: a sandbox that dies quietly leaves a hash claiming a run
that no longer exists, so the reaper and the handshake ordering get tested
hardest.

``test_cloudfactory_flow.py`` covers the same three verbs against the other
factory, and the pair is deliberately readable side by side.
"""

from __future__ import annotations

import pytest
from conftest import FINISHED_CANONICAL_FIELDS, WORKORDER_STREAM
from megadesk_contracts.wire import machine as wire
from megadesk_contracts.wire.signal import publish_fields

pytestmark = [pytest.mark.redis, pytest.mark.git]

TICKET = "add-widget-tests"
INSTRUCTIONS = "Cover the widget module with tests."


# --- helpers ---------------------------------------------------------------


def place_order(
    redis_client,
    git_floor,
    *,
    ticket_name: str = TICKET,
    ref: str = "",
) -> None:
    wire.publish_workorder(
        redis_client,
        wire.workorder_fields(
            repo=git_floor.repo,
            url=str(git_floor.origin),
            ticket_name=ticket_name,
            instructions=INSTRUCTIONS,
            ref=ref,
        ),
    )


def stored_ticket_id(redis_client, index: int = 0) -> str:
    return redis_client.xrange(WORKORDER_STREAM)[index][0]


def runs_on(redis_client) -> dict[str, dict[str, str]]:
    return {
        wire.guid_from_agent_handler_key(key): redis_client.hgetall(key)
        for key in redis_client.scan_iter(match=f"{wire.AGENTHANDLER_PREFIX}*")
    }


def finished(read_stream, git_floor) -> list[dict[str, str]]:
    return [
        fields for _id, fields in read_stream(wire.finished_stream(git_floor.repo))
    ]


# --- launching -------------------------------------------------------------


def test_an_order_starts_one_sandbox(
    machine_factory, fake_machine_factory, redis_client, git_floor, read_stream
) -> None:
    place_order(redis_client, git_floor)

    assert machine_factory.poll_orders() == 1

    ticket_id = stored_ticket_id(redis_client)
    assert len(fake_machine_factory.launches) == 1
    launch = fake_machine_factory.launches[0]
    assert launch["repo"] == git_floor.repo
    assert launch["ticket_name"] == TICKET
    assert launch["ticket_id"] == ticket_id
    assert launch["URL"] == str(git_floor.origin)
    assert launch["auto_pr"] is True
    assert "wt" not in launch
    assert "agent_dir" not in launch

    registered = runs_on(redis_client)
    assert list(registered) == [launch["run_key"]]
    fields = registered[launch["run_key"]]
    assert fields["ticket_id"] == ticket_id
    assert fields["status"] == wire.STATUS_RUNNING
    assert finished(read_stream, git_floor) == [], "a started run has not finished"


def test_the_registry_entry_exists_before_the_sandbox_starts(
    machine_factory, fake_machine_factory, redis_client, git_floor
) -> None:
    """The sandbox reads its own hash to find its work, so ordering is the contract.

    Asserted from inside ``launch``, because a hash written a moment later would
    still look correct afterwards while the container had already failed to find
    it.
    """
    seen: list[dict[str, str]] = []
    original = fake_machine_factory.launch

    def spy(order):
        seen.append(redis_client.hgetall(wire.agent_handler_key(order["run_key"])))
        return original(order)

    fake_machine_factory.launch = spy
    place_order(redis_client, git_floor)
    machine_factory.poll_orders()

    assert len(seen) == 1
    assert seen[0]["status"] == wire.STATUS_QUEUED
    assert seen[0]["ticket_id"]


def test_an_unusable_order_is_stored_rather_than_retried_forever(
    machine_factory, fake_machine_factory, redis_client
) -> None:
    publish_fields(
        redis_client, wire.WORKORDER_CHANNEL, {"repo": "", "ticket_name": ""}
    )

    assert machine_factory.poll_orders() == 0
    assert fake_machine_factory.launches == []
    assert redis_client.xlen(WORKORDER_STREAM) == 1


def test_a_stale_stream_entry_does_not_start_a_sandbox(
    machine_factory, fake_machine_factory, redis_client, git_floor
) -> None:
    redis_client.xadd(
        WORKORDER_STREAM,
        wire.workorder_fields(
            repo=git_floor.repo,
            url=str(git_floor.origin),
            ticket_name=TICKET,
            instructions=INSTRUCTIONS,
        ),
    )

    assert machine_factory.poll_orders() == 0
    assert fake_machine_factory.launches == []


def test_a_sandbox_that_never_started_is_reported_not_left_hanging(
    machine_factory, fake_machine_factory, redis_client, git_floor, read_stream
) -> None:
    """Silence would leave a FINISHED stream with no outcome for a run that died."""
    fake_machine_factory.startup_error = "docker daemon is not running"
    place_order(redis_client, git_floor)

    assert machine_factory.poll_orders() == 0
    ticket_id = stored_ticket_id(redis_client)

    reports = finished(read_stream, git_floor)
    assert len(reports) == 1
    assert set(reports[0]) == set(FINISHED_CANONICAL_FIELDS)
    assert reports[0]["ticket_id"] == ticket_id
    assert reports[0]["ticket_name"] == TICKET
    assert reports[0]["status"] == wire.STATUS_ERROR
    assert reports[0]["pr_url"] == ""
    assert runs_on(redis_client) == {}, "a failed launch leaves no live run"
    assert fake_machine_factory.released, "failed launch must release the sidecar"


# --- reaping a sandbox that died quietly ------------------------------------


def test_a_live_sandbox_is_left_alone(
    machine_factory, redis_client, git_floor
) -> None:
    place_order(redis_client, git_floor)
    machine_factory.poll_orders()

    assert machine_factory.poll_runs(force=True) == 0
    assert len(runs_on(redis_client)) == 1


def test_a_sandbox_that_vanished_is_reaped_and_reported(
    machine_factory, fake_machine_factory, redis_client, git_floor, read_stream
) -> None:
    """A container is not a managed service: nothing else notices it stopped."""
    place_order(redis_client, git_floor)
    machine_factory.poll_orders()
    ticket_id = stored_ticket_id(redis_client)
    run_key = fake_machine_factory.launches[0]["run_key"]

    fake_machine_factory.stop(run_key)

    assert machine_factory.poll_runs(force=True) == 1
    reports = finished(read_stream, git_floor)
    assert len(reports) == 1
    assert reports[0]["ticket_id"] == ticket_id
    assert reports[0]["status"] == wire.STATUS_ERROR
    assert reports[0]["pr_url"] == ""
    assert runs_on(redis_client) == {}
    assert machine_factory.poll_runs(force=True) == 0, "reaped once, not every poll"


def test_a_healthy_sandbox_reporting_for_itself_is_not_reaped_twice(
    machine_factory, fake_machine_factory, redis_client, git_floor, read_stream
) -> None:
    """The sandbox publishes from inside, where the exit code is. Then it is gone."""
    place_order(redis_client, git_floor)
    machine_factory.poll_orders()
    ticket_id = stored_ticket_id(redis_client)
    run_key = fake_machine_factory.launches[0]["run_key"]

    redis_client.xadd(
        wire.finished_stream(git_floor.repo),
        wire.finished_fields(
            ticket_name=TICKET,
            ticket_id=ticket_id,
            status=wire.STATUS_FINISHED,
            pr_url="https://github.com/acme/widgets/pull/1",
        ),
    )
    redis_client.delete(wire.agent_handler_key(run_key))
    fake_machine_factory.stop(run_key)

    assert machine_factory.poll_runs(force=True) == 0
    assert len(finished(read_stream, git_floor)) == 1, "one run, one FINISHED"


def test_a_sandbox_that_finished_cleanly_releases_its_sidecar(
    machine_factory, fake_machine_factory, redis_client, git_floor, read_stream
) -> None:
    """Happy path deletes the hash first, so sidecar cleanup cannot walk the registry."""
    place_order(redis_client, git_floor)
    machine_factory.poll_orders()
    ticket_id = stored_ticket_id(redis_client)
    run_key = fake_machine_factory.launches[0]["run_key"]

    redis_client.xadd(
        wire.finished_stream(git_floor.repo),
        wire.finished_fields(
            ticket_name=TICKET,
            ticket_id=ticket_id,
            status=wire.STATUS_FINISHED,
            pr_url="https://github.com/acme/widgets/pull/1",
        ),
    )
    redis_client.delete(wire.agent_handler_key(run_key))
    fake_machine_factory.stop(run_key)

    assert machine_factory.poll_sidecars() == 1
    assert fake_machine_factory.released == [run_key]
    assert machine_factory.poll_sidecars() == 0, "sidecar released once"
    assert machine_factory.poll_runs(force=True) == 0
    assert len(finished(read_stream, git_floor)) == 1, "one run, one FINISHED"


def test_a_live_sandbox_leaves_its_sidecar_alone(
    machine_factory, fake_machine_factory, redis_client, git_floor
) -> None:
    place_order(redis_client, git_floor)
    machine_factory.poll_orders()

    assert machine_factory.poll_sidecars() == 0
    assert fake_machine_factory.released == []
    assert len(runs_on(redis_client)) == 1


def test_a_run_whose_order_is_gone_is_dropped_rather_than_rechecked(
    machine_factory, fake_machine_factory, redis_client, git_floor, read_stream
) -> None:
    """With no order there is no ticket name, so nothing coherent to publish."""
    place_order(redis_client, git_floor)
    machine_factory.poll_orders()
    ticket_id = stored_ticket_id(redis_client)
    run_key = fake_machine_factory.launches[0]["run_key"]

    redis_client.xdel(WORKORDER_STREAM, ticket_id)
    fake_machine_factory.stop(run_key)

    assert machine_factory.poll_runs(force=True) == 0
    assert runs_on(redis_client) == {}, "the dead run must not be polled forever"
    assert finished(read_stream, git_floor) == []


# --- cancelling -------------------------------------------------------------


def test_cancelling_stops_the_sandbox_and_marks_the_run(
    machine_factory, fake_machine_factory, redis_client, git_floor
) -> None:
    place_order(redis_client, git_floor)
    machine_factory.poll_orders()
    run_key = fake_machine_factory.launches[0]["run_key"]

    assert machine_factory.cancel(run_key) is True

    assert fake_machine_factory.cancelled == [run_key]
    assert runs_on(redis_client)[run_key]["status"] == wire.STATUS_CANCELLED


def test_cancelling_a_run_that_does_not_exist_says_so(
    machine_factory, fake_machine_factory
) -> None:
    assert machine_factory.cancel("no-such-guid") is False
    assert fake_machine_factory.cancelled == []


def test_the_order_decides_which_branch_the_sandbox_starts_from(
    machine_factory, fake_machine_factory, redis_client, git_floor
) -> None:
    """AutoIntegrate's fix has to be built on the pull request's own branch."""
    place_order(
        redis_client, git_floor, ticket_name=TICKET, ref="cursor/stuck-branch"
    )
    place_order(redis_client, git_floor, ticket_name="ordinary-ticket")

    machine_factory.poll_orders()

    by_ticket = {run["ticket_name"]: run for run in fake_machine_factory.launches}
    assert by_ticket[TICKET]["ref"] == "cursor/stuck-branch"
    assert by_ticket["ordinary-ticket"]["ref"] == "", (
        "an order that names no branch must leave the default alone"
    )


def test_same_ticket_two_starts_do_not_remove_the_first_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second WORKORDER for the same ticket must not docker rm the live run."""
    from unittest.mock import MagicMock, patch

    from MachineFactoryManager import pool

    monkeypatch.setenv("GH_TOKEN", "test-token")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(pool, "ensure_factory_acl_user", lambda url=None: "acl-test-pw")

    run_names: list[str] = []
    docker_calls: list[list[str]] = []

    def _docker(args: list[str], *, check: bool = True) -> MagicMock:
        docker_calls.append(list(args))
        result = MagicMock()
        result.returncode = 1
        result.stdout = ""
        if args and args[0] == "run" and "--name" in args:
            run_names.append(args[args.index("--name") + 1])
            result.returncode = 0
        return result

    with patch.object(pool, "_docker", side_effect=_docker), patch.object(
        pool, "start_redis_sidecar", return_value="mf-redis-test"
    ), patch.object(pool, "ensure_network"), patch.object(
        pool, "_follow_container_logs"
    ), patch.object(pool, "remove_container") as remove:
        try:
            first = pool.start_ticket_sandbox(
                repo="widgets",
                ticket=TICKET,
                repo_url="https://github.com/acme/widgets",
                guid="guid-aaa",
                ticket_id="1-0",
                api_key="key",
            )
            second = pool.start_ticket_sandbox(
                repo="widgets",
                ticket=TICKET,
                repo_url="https://github.com/acme/widgets",
                guid="guid-bbb",
                ticket_id="1-1",
                api_key="key",
            )
        finally:
            pool.cleanup_sandbox_secrets("guid-aaa")
            pool.cleanup_sandbox_secrets("guid-bbb")

    assert first != second
    assert run_names == [first, second]
    assert first == pool.container_name("widgets", TICKET, "guid-aaa")
    assert second == pool.container_name("widgets", TICKET, "guid-bbb")
    remove.assert_not_called()
    assert not any(call[0] == "rm" for call in docker_calls if call)


def test_same_guid_leftover_may_be_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A crashed leftover of this run's name can be rm'd; no other name."""
    from unittest.mock import MagicMock, patch

    from MachineFactoryManager import pool

    monkeypatch.setenv("GH_TOKEN", "test-token")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(pool, "ensure_factory_acl_user", lambda url=None: "acl-test-pw")

    leftover = pool.container_name("widgets", TICKET, "guid-01")
    other = pool.container_name("widgets", TICKET, "guid-other")
    removed: list[str] = []

    def _docker(args: list[str], *, check: bool = True) -> MagicMock:
        result = MagicMock()
        result.stdout = ""
        if args and args[0] == "inspect":
            result.returncode = 0 if leftover in args else 1
        elif args and args[0] == "rm":
            removed.extend(arg for arg in args if arg not in {"rm", "-f"})
            result.returncode = 0
        else:
            result.returncode = 0
        return result

    with patch.object(pool, "_docker", side_effect=_docker), patch.object(
        pool, "start_redis_sidecar", return_value="mf-redis-test"
    ), patch.object(pool, "ensure_network"), patch.object(
        pool, "_follow_container_logs"
    ):
        try:
            started = pool.start_ticket_sandbox(
                repo="widgets",
                ticket=TICKET,
                repo_url="https://github.com/acme/widgets",
                guid="guid-01",
                ticket_id="1-0",
                api_key="key",
            )
        finally:
            pool.cleanup_sandbox_secrets("guid-01")

    assert started == leftover
    assert removed == [leftover]
    assert other not in removed


def test_the_sandbox_environment_carries_the_ref_and_defaults_to_dev(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``STARTING_REF`` is what AgentHandler clones and bases its PR on."""
    from unittest.mock import patch

    from megadesk_contracts import FACTORY_ACL_USER
    from MachineFactoryManager import pool

    planted = "gho_planted_fake_token_for_redact"
    monkeypatch.setenv("GH_TOKEN", planted)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(pool, "ensure_factory_acl_user", lambda url=None: "acl-test-pw")

    def run_args(ref: str) -> list[str]:
        with patch.object(pool, "_docker") as docker, patch.object(
            pool, "start_redis_sidecar", return_value="mf-redis-test"
        ), patch.object(pool, "ensure_network"), patch.object(
            pool, "_follow_container_logs"
        ):
            docker.return_value.returncode = 1
            try:
                pool.start_ticket_sandbox(
                    repo="widgets",
                    ticket=TICKET,
                    repo_url="https://github.com/acme/widgets",
                    guid="guid-01",
                    ticket_id="1-0",
                    ref=ref,
                    api_key="key",
                )
            finally:
                pool.cleanup_sandbox_secrets("guid-01")
            return list(docker.call_args[0][0])

    def env_of(args: list[str]) -> dict[str, str]:
        return dict(
            pair.split("=", 1)
            for index, pair in enumerate(args)
            if index and args[index - 1] == "-e"
        )

    args = run_args("cursor/stuck-branch")
    env = env_of(args)
    joined = " ".join(args)
    assert env["STARTING_REF"] == "cursor/stuck-branch"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert "GH_TOKEN=" not in joined
    assert "GITHUB_TOKEN=" not in joined
    assert planted not in joined
    assert env["MEGADESK_FACTORY_REDIS_URL"].startswith(
        f"redis://{FACTORY_ACL_USER}:"
    )
    assert env["MEGADESK_GITHUB_TOKEN_FILE"]
    assert env["GIT_ASKPASS"]
    assert env_of(run_args(""))["STARTING_REF"] == wire.DEFAULT_STARTING_REF


def test_auto_pr_sandbox_refuses_to_start_without_github_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import patch

    from MachineFactoryManager import pool

    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(pool, "resolve_github_token", lambda: "")

    with patch.object(pool, "start_redis_sidecar") as sidecar:
        with pytest.raises(RuntimeError, match="GH_TOKEN"):
            pool.start_ticket_sandbox(
                repo="widgets",
                ticket=TICKET,
                repo_url="https://github.com/acme/widgets",
                guid="guid-01",
                ticket_id="1-0",
                api_key="key",
            )
    sidecar.assert_not_called()


def test_resolve_github_token_falls_back_to_gh_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from MachineFactoryManager import pool

    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    class Result:
        returncode = 0
        stdout = "gho_from_cli\n"
        stderr = ""

    monkeypatch.setattr(
        pool.subprocess,
        "run",
        lambda *args, **kwargs: Result(),
    )
    assert pool.resolve_github_token() == "gho_from_cli"


def test_publish_branch_fails_clearly_without_a_token(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from AgentHandler.repo_clone import SandboxRepo, _auth_url

    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("MEGADESK_GITHUB_TOKEN_FILE", raising=False)
    assert _auth_url("https://github.com/acme/widgets") == "https://github.com/acme/widgets"

    planted = "gho_planted_fake_token_for_redact"
    monkeypatch.setenv("GH_TOKEN", planted)
    assert _auth_url("https://github.com/acme/widgets") == "https://github.com/acme/widgets"
    assert planted not in _auth_url("https://github.com/acme/widgets")

    monkeypatch.delenv("GH_TOKEN", raising=False)
    repo = SandboxRepo(
        tmp_path,
        repo_url="https://github.com/acme/widgets",
        ticket="smoke",
    )
    with pytest.raises(RuntimeError, match="GH_TOKEN is not set"):
        repo.publish_branch()


def test_clone_url_and_ref_allowlists() -> None:
    from megadesk_contracts import allowlisted_clone_source, validate_git_ref
    from MachineFactoryManager import pool

    assert allowlisted_clone_source(
        "https://github.com/acme/widgets.git", allow_local=False
    ) == ("https://github.com/acme/widgets", "widgets")
    assert allowlisted_clone_source(
        "git@github.com:acme/widgets.git", allow_local=False
    ) == ("https://github.com/acme/widgets", "widgets")
    with pytest.raises(ValueError):
        allowlisted_clone_source("https://example.com/acme/widgets", allow_local=False)
    with pytest.raises(ValueError):
        allowlisted_clone_source("file:///tmp/widgets", allow_local=False)

    assert validate_git_ref("") == wire.DEFAULT_STARTING_REF
    assert validate_git_ref("cursor/stuck-branch") == "cursor/stuck-branch"
    with pytest.raises(ValueError):
        validate_git_ref("--upload-pack=evil")
    with pytest.raises(ValueError):
        validate_git_ref("has space")
    with pytest.raises(ValueError):
        validate_git_ref("has\nnewline")

    with pytest.raises(RuntimeError):
        pool.start_ticket_sandbox(
            repo="widgets",
            ticket=TICKET,
            repo_url="https://example.com/acme/widgets",
            guid="guid-bad-url",
            ticket_id="1-0",
            api_key="key",
            auto_pr=False,
        )
    with pytest.raises(RuntimeError):
        pool.start_ticket_sandbox(
            repo="widgets",
            ticket=TICKET,
            repo_url="https://github.com/acme/widgets",
            guid="guid-bad-ref",
            ticket_id="1-0",
            ref="--upload-pack=evil",
            api_key="key",
            auto_pr=False,
        )


def test_docker_errors_redact_planted_tokens() -> None:
    from MachineFactoryManager import pool

    planted = "gho_planted_fake_token_for_redact"
    message = (
        f"docker run -e GH_TOKEN={planted} -e "
        f"MEGADESK_FACTORY_REDIS_URL=redis://megadesk-factory:{planted}@host/0 "
        f"failed: x-access-token:{planted}@github.com"
    )
    redacted = pool.redact_secrets(message, planted)
    assert planted not in redacted
    assert "GH_TOKEN=***" in redacted


def test_a_repo_only_needs_a_dev_branch(git_floor) -> None:
    """Factories start from ``dev``. ``main`` and ``agents`` are not required."""
    from megadesk_contracts.wire.factory import DEFAULT_STARTING_REF

    assert DEFAULT_STARTING_REF == "dev"
    assert git_floor.origin_sha("dev")
    assert git_floor.current_branch(git_floor.dev_dir) == "dev"
    assert not git_floor.origin_sha("agents")
    assert not git_floor.origin_sha("main")


# --- the frontend ----------------------------------------------------------


@pytest.mark.canvas
def test_the_monitor_shows_orders_live_agents_and_sandboxes_not_logs(
    harness, redis_client, machine_wire
) -> None:
    redis_client.xadd(
        WORKORDER_STREAM,
        machine_wire.workorder_fields(
            repo="widgets",
            url="https://github.com/acme/widgets.git",
            ticket_name=TICKET,
            instructions=INSTRUCTIONS,
        ),
    )
    redis_client.hset(
        machine_wire.agent_handler_key("live-guid-01"),
        mapping=machine_wire.agent_handler_fields(
            ticket_id="1-0",
            status=wire.STATUS_RUNNING,
        ),
    )
    fe = harness.drop("machine_factory")
    hosted = harness.model.members[fe.member_id]
    assert hosted.height <= 120
    assert hosted.width <= 420

    assert fe.exists("queue_list")
    assert fe.exists("live_list")
    assert fe.exists("docker_list")
    assert fe.exists("error_lamp")
    assert not fe.exists("floor_list")
    assert not fe.exists("log")
    assert not fe.exists("status_lbl")
    assert not fe.exists("redis_dot")
    assert not fe.exists("docker_dot")
    assert not fe.exists("floor_path")
    assert not fe.exists("detail")

    harness.wait_until(
        lambda: any(TICKET in item for item in fe.items("queue_list")),
        message="the processed work order to appear",
    )
    harness.wait_until(
        lambda: any("running" in item for item in fe.items("live_list")),
        message="the live agent to appear",
    )
