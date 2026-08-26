"""The one thing both factories must keep: being interchangeable.

A graph controller is meant to place an agent locally or in the cloud without its
own logic forking on which it got. That only holds if the two runtimes really do
present the same three verbs and report in the same words, so this file asserts it
directly rather than trusting that both suites happening to pass implies it.

No GUI, no Redis, no Docker, no API key: ``pytest tests/test_factory_protocol.py``.
"""

from __future__ import annotations

import inspect

import pytest
from megadesk_contracts import AgentFactory, RunHandle, RunStatus
from megadesk_contracts.testing import FakeCloudFactory, FakeMachineFactory
from megadesk_contracts.wire import cloud as cloud_wire
from megadesk_contracts.wire import factory as factory_wire
from megadesk_contracts.wire import machine as machine_wire


def real_runtimes() -> list[type]:
    from CloudFactoryManager.runtime import CursorCloudFactory
    from MachineFactoryManager.runtime import DockerSandboxFactory

    return [DockerSandboxFactory, CursorCloudFactory]


@pytest.mark.parametrize("verb", ["launch", "poll", "cancel"])
def test_both_runtimes_present_the_same_three_verbs(verb: str) -> None:
    """Same names, same parameters — a caller cannot tell them apart."""
    signatures = {
        runtime.__name__: inspect.signature(getattr(runtime, verb))
        for runtime in real_runtimes()
    }
    parameters = {
        name: [p for p in sig.parameters if p != "self"]
        for name, sig in signatures.items()
    }
    assert len(set(map(tuple, parameters.values()))) == 1, parameters


def test_both_runtimes_satisfy_the_protocol() -> None:
    for runtime in real_runtimes():
        assert isinstance(runtime.__new__(runtime), AgentFactory), runtime.__name__


def test_both_fakes_satisfy_the_protocol() -> None:
    """Otherwise a suite could pass against a shape production does not have."""
    assert isinstance(FakeMachineFactory(), AgentFactory)
    assert isinstance(FakeCloudFactory(), AgentFactory)


def test_a_caller_can_drive_either_fake_with_one_code_path() -> None:
    """The point of the protocol, exercised: launch, poll to terminal, read result.

    ``settle`` is where the two honestly differ, and it is not the caller's code: a
    cloud run ends when Cursor says so, while a container ends when it exits. Both
    are the world moving on, not a branch the graph has to write.
    """

    def run_to_completion(fac: AgentFactory, order: dict, settle) -> RunStatus:
        handle = fac.launch(order)
        assert isinstance(handle, RunHandle)
        assert handle.run_key
        for _attempt in range(5):
            state = fac.poll(handle.run_key)
            assert isinstance(state, RunStatus)
            assert state.status in factory_wire.RUN_STATUSES
            if factory_wire.is_terminal(state.status):
                return state
            settle(handle.run_key)
        raise AssertionError(f"{type(fac).__name__} never reached a terminal status")

    machine_fake = FakeMachineFactory()
    machine = run_to_completion(
        machine_fake,
        {
            "run_key": "guid-001",
            "repo": "widgets",
            "ticket_name": "add-widget-tests",
            "URL": "https://github.com/acme/widgets.git",
            "auto_pr": True,
            "ticket_id": "1-0",
            "instructions": "Cover the widget module with tests.",
        },
        settle=machine_fake.stop,
    )
    assert machine_fake.launches[0]["URL"].endswith("widgets.git")
    assert machine_fake.launches[0]["auto_pr"] is True
    assert "wt" not in machine_fake.launches[0]
    assert "agent_dir" not in machine_fake.launches[0]
    cloud = run_to_completion(
        FakeCloudFactory(polls_before_finish=0),
        {
            "repo_url": "https://github.com/acme/widgets",
            "title": "Document the frame pump reset",
            "instructions": "Explain why the frame pump needs a reset.",
        },
        settle=lambda _run_key: None,
    )

    assert machine.status == factory_wire.STATUS_FINISHED
    assert cloud.status == factory_wire.STATUS_FINISHED
    assert cloud.result.startswith("https://"), "the cloud's result is its PR"


def test_both_families_report_in_the_same_status_words() -> None:
    """A graph reading `finished` must not have to ask which factory said it."""
    assert machine_wire.RUN_STATUSES is factory_wire.RUN_STATUSES
    assert cloud_wire.RUN_STATUSES is factory_wire.RUN_STATUSES
    assert machine_wire.STATUS_FINISHED == cloud_wire.STATUS_FINISHED
    assert machine_wire.normalize_status is cloud_wire.normalize_status


def test_an_unknown_provider_status_never_reads_as_finished() -> None:
    """Closing a run early is worse than watching one a little too long."""
    for word in ("CREATING", "something new", "", "PROVISIONING"):
        assert factory_wire.normalize_status(word) == factory_wire.STATUS_RUNNING
        assert not factory_wire.is_terminal(factory_wire.normalize_status(word))
