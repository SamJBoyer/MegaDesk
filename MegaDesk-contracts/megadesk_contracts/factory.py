"""The normalized shape of a place that runs an agent for you.

A Factory is whatever turns an order into a working agent: MachineFactory builds
a git worktree and a container on this machine, CloudFactory asks Cursor for a VM.
Both are then asked the same three questions — start this, where has it got to,
stop it — so a graph can hand work to either without its own logic forking on
which one it got.

Three verbs and two shapes:

* ``launch`` returns a :class:`RunHandle` or raises. The raise is load-bearing:
  ``AgentStartupError`` means nothing started and a retry is safe, any other
  ``AgentError`` means something may have started and a retry could duplicate it.
* ``poll`` returns a :class:`RunStatus` whose ``status`` is one of
  ``megadesk_contracts.wire.factory.RUN_STATUSES``, shared across both families.
* ``cancel`` stops a run by the same key ``launch`` handed back.

An order is a mapping rather than a fixed dataclass because the two families
genuinely need different inputs, and pretending otherwise would mean a machine
order carrying an ``auto_pr`` it cannot honor. Every order shares ``title``,
``instructions`` and ``model``; beyond that each family adds what its
infrastructure actually needs, validated by its own ``wire`` module —
``wire.machine.parse_workorder`` or ``wire.cloud.parse_cloudorder`` — before it
ever reaches a runtime.

Writing both nodes against this surface is also what makes them testable without
a Docker daemon or a paid Cursor VM; see ``megadesk_contracts.testing.fakes``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, runtime_checkable


@dataclass
class RunHandle:
    """What a factory hands back once an agent exists somewhere.

    ``run_key`` is the id the factory's own registry is keyed by — a sandbox guid
    for MachineFactory, Cursor's ``bc-`` agent id for CloudFactory — and it is
    the only handle ``poll`` and ``cancel`` accept. ``run_id`` is the provider's
    separate id for the run itself, when there is one worth keeping for support.
    """

    run_key: str
    run_id: str = ""


@dataclass
class RunStatus:
    """Where a launched run has got to, and what it produced.

    ``result`` is the run's addressable output: a pull request URL from the
    cloud, an absolute worktree path from a machine. Both are "go here to see
    what happened", which is as much as a graph needs to route the next step.
    """

    status: str
    result: str = ""
    detail: str = ""


@runtime_checkable
class AgentFactory(Protocol):
    """What a Factory node needs from whoever actually runs the agent."""

    def launch(self, order: Mapping[str, Any]) -> RunHandle: ...

    def poll(self, run_key: str) -> RunStatus: ...

    def cancel(self, run_key: str) -> None: ...
