"""Run an agent in a sandbox on this machine, behind the shared Factory surface.

This is MachineFactory's answer to the same three questions CloudFactory answers
with the Cursor SDK — launch this, where has it got to, stop it — so a graph can
place work on either without forking its own logic on which one it got. See
``megadesk_contracts.factory``.

The local harness clones the target repo into a Docker sandbox, gives the agent
its own Redis sidecar for MegaDesk, and keeps factory IPC on the host pair via
``MEGADESK_FACTORY_REDIS_URL``. When the work lands, AgentHandler opens a pull
request — the same addressable result CloudFactory hands back.

A container is not a managed service. Nobody else notices when one dies, so
``poll`` exists mainly to catch a sandbox that vanished without reporting.
``poll`` reports only what Docker knows: is the sandbox still up. The outcome
is published by AgentHandler onto ``FINISHED:<REPO>``.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Mapping

from megadesk_contracts import (
    AgentStartupError,
    RunHandle,
    RunStatus,
    resolve_redis_url,
)
from megadesk_contracts.wire import factory as status_wire

from MachineFactoryManager.pool import (
    container_for_run,
    container_is_running,
    remove_container,
    start_ticket_sandbox,
    stop_redis_sidecar,
)

log = logging.getLogger("runtime")


class DockerSandboxFactory:
    """Launch agents as one-shot AgentHandler containers with a Redis sidecar."""

    def __init__(self, redis_url: str | None = None) -> None:
        self.redis_url = resolve_redis_url(redis_url)

    def launch(self, order: Mapping[str, Any]) -> RunHandle:
        """Start a sandbox for one prepared order.

        ``order`` carries the parsed WORKORDER plus ``ticket_id`` and ``run_key``.
        The AGENTHANDLER hash has to exist before the container starts, since the
        container finds its own work by reading it.
        """
        run_key = str(order.get("run_key") or "").strip() or uuid.uuid4().hex
        try:
            container = start_ticket_sandbox(
                repo=order["repo"],
                ticket=order["ticket_name"],
                repo_url=str(order.get("URL") or order.get("repo_url") or ""),
                ref=str(order.get("ref") or ""),
                guid=run_key,
                ticket_id=order["ticket_id"],
                auto_pr=bool(order.get("auto_pr", True)),
            )
        except SystemExit:
            # A missing CURSOR_API_KEY is a broken installation, not a bad order.
            stop_redis_sidecar(run_key)
            raise
        except Exception as exc:  # noqa: BLE001 - the docker CLI raises broadly
            stop_redis_sidecar(run_key)
            raise AgentStartupError(f"Could not start sandbox: {exc}") from exc
        return RunHandle(run_key=run_key, run_id=container)

    def poll(self, run_key: str) -> RunStatus:
        """Running while the sandbox is up; finished once Docker has let it go."""
        container = container_for_run(run_key)
        if container and container_is_running(container):
            return RunStatus(status=status_wire.STATUS_RUNNING, detail=container)
        return RunStatus(
            status=status_wire.STATUS_FINISHED,
            detail="sandbox is no longer running",
        )

    def cancel(self, run_key: str) -> None:
        container = container_for_run(run_key)
        if not container:
            log.info("No sandbox left to cancel for run %s", run_key)
            self.release(run_key)
            return
        log.info("Removing sandbox %s for run %s", container, run_key)
        remove_container(container)
        self.release(run_key)

    def release(self, run_key: str) -> None:
        """Drop the Redis sidecar for a run that is no longer using it."""
        stop_redis_sidecar(run_key)
