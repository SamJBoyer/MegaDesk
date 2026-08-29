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


def test_the_sandbox_environment_carries_the_ref_and_defaults_to_dev() -> None:
    """``STARTING_REF`` is what AgentHandler clones and bases its PR on."""
    from unittest.mock import patch

    from MachineFactoryManager import pool

    def env_of(ref: str) -> dict[str, str]:
        with patch.object(pool, "_docker") as docker, patch.object(
            pool, "start_redis_sidecar", return_value="mf-redis-test"
        ), patch.object(pool, "ensure_network"), patch.object(
            pool, "_follow_container_logs"
        ):
            docker.return_value.returncode = 1
            pool.start_ticket_sandbox(
                repo="widgets",
                ticket=TICKET,
                repo_url="https://github.com/acme/widgets",
                guid="guid-01",
                ticket_id="1-0",
                ref=ref,
                api_key="key",
            )
            args = docker.call_args[0][0]
        return dict(
            pair.split("=", 1)
            for index, pair in enumerate(args)
            if index and args[index - 1] == "-e"
        )

    assert env_of("cursor/stuck-branch")["STARTING_REF"] == "cursor/stuck-branch"
    assert env_of("")["STARTING_REF"] == wire.DEFAULT_STARTING_REF


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
