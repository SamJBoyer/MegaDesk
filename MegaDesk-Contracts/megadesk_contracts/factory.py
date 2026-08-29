"""The normalized shape of a place that runs an agent for you.

A Factory is whatever turns an order into a working agent: MachineFactory clones
into a Docker sandbox (with a Redis sidecar) on this machine, CloudFactory asks
Cursor for a VM. Both are then asked the same three questions — start this,
where has it got to, stop it — so a graph can hand work to either without its
own logic forking on which one it got. Both produce a pull-request URL as
``RunStatus.result``.

Three verbs and two shapes:

* ``launch`` returns a :class:`RunHandle` or raises. The raise is load-bearing:
  ``AgentStartupError`` means nothing started and a retry is safe, any other
  ``AgentError`` means something may have started and a retry could duplicate it.
* ``poll`` returns a :class:`RunStatus` whose ``status`` is one of
  ``megadesk_contracts.wire.factory.RUN_STATUSES``, shared across both families.
* ``cancel`` stops a run by the same key ``launch`` handed back.

An order is a mapping rather than a fixed dataclass because the two families
genuinely need different inputs. Every order shares ``instructions`` and
``model``; beyond that each family adds what its infrastructure actually needs,
validated by its own ``wire`` module — ``wire.machine.parse_workorder`` or
``wire.cloud.parse_cloudorder`` — before it ever reaches a runtime. A machine
order needs ``repo`` / ``URL`` / ``ticket_name`` (and ``auto_pr``); a cloud
order needs ``repo_url`` / ``auto_pr`` / ``ref``.

Writing both nodes against this surface is also what makes them testable without
a Docker daemon or a paid Cursor VM; see ``megadesk_contracts.testing.fakes``.
"""

from __future__ import annotations

from collections.abc import Sequence
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

    ``result`` is the run's addressable output: a pull-request URL from either
    factory. That is "go here to see what happened", which is as much as a
    graph needs to route the next step.
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


def prompt_payload(text: str, pictures: Sequence[str] = ()) -> Any:
    """What ``agent.send`` receives: a string, or text plus image URLs.

    A bare string stays the payload when the order has no pictures, so
    existing callers and test fakes keep working. Cursor accepts a
    ``UserMessage`` (or a dict with ``images``) when reference pictures
    need to travel with the prompt.
    """
    urls = [str(url).strip() for url in pictures if str(url).strip()]
    if not urls:
        return text
    try:
        from cursor_sdk import SDKImage, UserMessage
    except ImportError:
        return {"text": text, "images": [{"url": url} for url in urls]}
    builder = getattr(SDKImage, "url_image", None)
    if callable(builder):
        return UserMessage(text=text, images=[builder(url) for url in urls])
    return {"text": text, "images": [{"url": url} for url in urls]}
