"""Run an agent in a sandbox on this machine, behind the shared Factory surface.

This is MachineFactory's answer to the same three questions CloudFactory answers
with the Cursor SDK — launch this, where has it got to, stop it — so a graph can
place work on either without forking its own logic on which one it got. See
``megadesk_contracts.factory``.

The local advantage is total control of the harness: the agent runs inside
AgentHandler, in a container we build, against a real git worktree. The local cost
is that a container is not a managed service. Nobody else notices when one dies,
so ``poll`` exists mainly to catch a sandbox that vanished without reporting —
which for a cloud run is the provider's problem and for this one is ours.

``poll`` deliberately reports only what Docker knows: is the sandbox still up.
The *outcome* of a machine run is published by AgentHandler itself onto
``FINISHED:<REPO>``, from inside the container, where the exit code and the
transcript actually are. Second-guessing that from out here would give two
answers to one question.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any, Mapping

from megadesk_contracts import (
    AgentStartupError,
    LaneBusyError,
    RunHandle,
    RunStatus,
    allocate_lane,
    refresh_lane,
    release_lane,
    resolve_redis_pair,
    resolve_redis_url,
)
from megadesk_contracts.wire import factory as status_wire

from MachineFactoryManager.pool import (
    container_for_run,
    container_is_running,
    remove_container,
    start_ticket_sandbox,
)

log = logging.getLogger("runtime")


class DockerSandboxFactory:
    """Launch agents as one-shot AgentHandler containers over mounted worktrees."""

    def __init__(self, redis_url: str | None = None) -> None:
        self.redis_url = resolve_redis_url(redis_url)

    def launch(self, order: Mapping[str, Any]) -> RunHandle:
        """Start a sandbox for one prepared order.

        ``order`` carries the parsed WORKORDER plus what the manager resolved
        while preparing the Floor: ``wt``, ``agent_dir`` and ``ticket_id``. It
        also carries ``run_key``, because this factory's handshake runs the other
        way round from the cloud's — the AGENTHANDLER hash has to exist before the
        container starts, since the container finds its own work by reading it.
        """
        run_key = str(order.get("run_key") or "").strip() or uuid.uuid4().hex
        try:
            ephemeral_db, _persistent_db = allocate_lane(
                owner=run_key, redis_url=self.redis_url
            )
        except LaneBusyError as exc:
            raise AgentStartupError(str(exc)) from exc
        factory_ephemeral, _ = resolve_redis_pair(self.redis_url)
        try:
            container = start_ticket_sandbox(
                repo=order["repo"],
                ticket=order["ticket_name"],
                host_worktree=Path(order["wt"]),
                agent_dir=Path(order["agent_dir"]),
                guid=run_key,
                ticket_id=order["ticket_id"],
                ephemeral_db=ephemeral_db,
                factory_ephemeral_db=factory_ephemeral,
            )
        except SystemExit:
            # A missing CURSOR_API_KEY is a broken installation, not a bad order.
            # Letting it through stops the BE instead of failing orders forever.
            release_lane(owner=run_key, redis_url=self.redis_url)
            raise
        except Exception as exc:  # noqa: BLE001 - the docker CLI raises broadly
            release_lane(owner=run_key, redis_url=self.redis_url)
            raise AgentStartupError(f"Could not start sandbox: {exc}") from exc
        return RunHandle(run_key=run_key, run_id=container)

    def poll(self, run_key: str) -> RunStatus:
        """Running while the sandbox is up; finished once Docker has let it go."""
        container = container_for_run(run_key)
        if container and container_is_running(container):
            refresh_lane(owner=run_key, redis_url=self.redis_url)
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
        """Drop the Redis lane for a run that is no longer using it."""
        release_lane(owner=run_key, redis_url=self.redis_url)
