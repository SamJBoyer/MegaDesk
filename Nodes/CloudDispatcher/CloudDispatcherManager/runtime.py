"""Cursor's cloud runtime: an agent on someone else's machine.

The difference from the local agents MissionControl runs is one keyword. Cursor
clones the repository onto its own VM, works there, pushes a branch, and opens a
pull request, so the input is a URL and the output is a link — there is no
worktree to hand over and nothing for MergeManager to merge afterwards.

Two consequences shape everything below:

* **The agent sees the pushed remote, not your working tree.** Uncommitted work
  is invisible to it, which is why the dispatcher never sends a local path.
* **The run outlives this process.** ``bc-`` ids are durable, so a restarted BE
  reattaches with ``Agent.get`` rather than losing track of a running agent.

``cursor_sdk`` is imported lazily so the FE, the wire tests, and the dispatcher's
own logic can all import this module without the SDK installed.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from megadesk_contracts import (
    AgentError,
    AgentRunError,
    AgentStartupError,
    CloudLaunch,
    CloudStatus,
)
from megadesk_contracts.wire import cloud as wire

log = logging.getLogger("cloud_dispatcher.runtime")

DEFAULT_MODEL = wire.DEFAULT_MODEL

# Cloud agents run unattended, so the prompt has to carry what a person would
# otherwise supply in review: stay small, and leave the branch in a state a
# reviewer can read in one sitting.
CLOUD_PROMPT = """{instructions}

Keep this change small and self-contained. Touch only what the task needs, do \
not reformat unrelated code, and do not add dependencies. Write a commit message \
that says what changed and why, titled: {title}"""

# Cursor's own status vocabulary, mapped onto ours. Anything unrecognized is
# treated as still running, because guessing "finished" would close a run that is
# still writing to a branch.
_STATUS_MAP = {
    "finished": wire.STATUS_FINISHED,
    "completed": wire.STATUS_FINISHED,
    "success": wire.STATUS_FINISHED,
    "error": wire.STATUS_ERROR,
    "failed": wire.STATUS_ERROR,
    "cancelled": wire.STATUS_CANCELLED,
    "canceled": wire.STATUS_CANCELLED,
    "expired": wire.STATUS_ERROR,
    "running": wire.STATUS_RUNNING,
    "creating": wire.STATUS_RUNNING,
    "pending": wire.STATUS_RUNNING,
    "queued": wire.STATUS_RUNNING,
}


def prompt_for(*, instructions: str, title: str) -> str:
    return CLOUD_PROMPT.format(instructions=instructions.strip(), title=title.strip())


def normalize_status(raw: Any) -> str:
    return _STATUS_MAP.get(str(raw or "").strip().lower(), wire.STATUS_RUNNING)


def _first_attr(obj: Any, *names: str) -> str:
    """Read the first attribute that exists, camelCase or snake_case.

    The SDK is young enough that both spellings appear across versions, and a
    dispatcher that missed a PR URL over a capital letter would look broken in a
    way that takes an hour to find.
    """
    for name in names:
        value = getattr(obj, name, None)
        if value:
            return str(value)
        if isinstance(obj, dict) and obj.get(name):
            return str(obj[name])
    return ""


class CursorCloudRuntime:
    """Launch and follow Cursor cloud agents."""

    def __init__(
        self, *, api_key: Optional[str] = None, model: str = DEFAULT_MODEL
    ) -> None:
        self.api_key = (
            api_key if api_key is not None else os.environ.get("CURSOR_API_KEY")
        )
        self.model = (model or DEFAULT_MODEL).strip() or DEFAULT_MODEL

    # --- launching ---

    def launch(
        self,
        *,
        repo_url: str,
        instructions: str,
        title: str,
        model: str = "",
        auto_pr: bool = True,
        ref: str = "",
    ) -> CloudLaunch:
        Agent, CloudAgentOptions = self._sdk()
        options: dict[str, Any] = {
            "repos": [repo_url],
            # Unattended runs must not page a human, and a run with no PR is a
            # branch nobody will ever find again.
            "auto_create_pr": bool(auto_pr),
            "skip_reviewer_request": True,
        }
        if ref:
            options["ref"] = ref

        chosen = (model or self.model).strip() or DEFAULT_MODEL
        log.info("Launching cloud agent model=%s repo=%s", chosen, repo_url)
        try:
            agent = Agent.create(
                model=chosen,
                api_key=self.api_key,
                # Always explicit: with neither runtime set the SDK quietly runs
                # locally, which would run a "cloud" job on this machine.
                cloud=CloudAgentOptions(**options),
            )
        except Exception as exc:  # noqa: BLE001
            raise self._startup_error(exc, "cloud agent could not be created") from exc

        agent_id = _first_attr(agent, "agent_id", "agentId", "id")
        if not agent_id:
            raise AgentStartupError("Cursor returned an agent with no id")

        try:
            run = agent.send(prompt_for(instructions=instructions, title=title))
        except Exception as exc:  # noqa: BLE001
            # The agent exists but has no work: cancel it rather than leaving a
            # billable idle VM behind.
            self._quiet_cancel(agent)
            raise self._startup_error(exc, f"agent {agent_id} would not accept work")

        run_id = _first_attr(run, "id", "run_id", "runId")
        log.info("Cloud agent %s running (run=%s)", agent_id, run_id or "?")
        return CloudLaunch(agent_id=agent_id, run_id=run_id)

    # --- following ---

    def poll(self, agent_id: str) -> CloudStatus:
        """Where the run is now. Reattaches by id, so a restart loses nothing."""
        agent = self._get(agent_id)
        status = normalize_status(
            _first_attr(agent, "status", "state") or self._run_status(agent)
        )
        pr_url = self._pr_url(agent)
        if pr_url and status == wire.STATUS_RUNNING:
            # A PR exists, so the work landed; some versions report the agent as
            # running until its VM is reaped.
            status = wire.STATUS_FINISHED
        return CloudStatus(
            status=status,
            pr_url=pr_url,
            detail=_first_attr(agent, "error", "message"),
        )

    def cancel(self, agent_id: str) -> None:
        agent = self._get(agent_id)
        for name in ("cancel", "stop", "close"):
            method = getattr(agent, name, None)
            if callable(method):
                method()
                log.info("Cancelled cloud agent %s via %s()", agent_id, name)
                return
        raise AgentRunError(f"Cursor agent {agent_id} cannot be cancelled")

    # --- models ---

    def models(self) -> list[str]:
        """Model ids the account can use, for the FE combo.

        Empty on any failure: an unreachable model list is a cosmetic problem,
        and hardcoding ids is how a combo ends up offering models that were
        retired months ago.
        """
        try:
            from cursor_sdk import Cursor
        except ImportError:
            return []
        try:
            client = Cursor(api_key=self.api_key)
            listed = client.models.list()
        except Exception:  # noqa: BLE001
            log.debug("Could not list models", exc_info=True)
            return []

        raw = getattr(listed, "models", None) or listed
        names: list[str] = []
        for item in raw or []:
            name = item if isinstance(item, str) else _first_attr(item, "id", "name")
            if name:
                names.append(str(name))
        return names

    # --- plumbing ---

    def _sdk(self) -> tuple[Any, Any]:
        try:
            from cursor_sdk import Agent, CloudAgentOptions
        except ImportError as exc:
            raise AgentStartupError(
                "cursor-sdk is not installed in this environment"
            ) from exc
        return Agent, CloudAgentOptions

    def _get(self, agent_id: str) -> Any:
        Agent, _options = self._sdk()
        try:
            return Agent.get(agent_id, api_key=self.api_key)
        except Exception as exc:  # noqa: BLE001
            raise AgentRunError(f"Could not read agent {agent_id}: {exc}") from exc

    @staticmethod
    def _run_status(agent: Any) -> str:
        run = getattr(agent, "run", None) or getattr(agent, "latest_run", None)
        return _first_attr(run, "status", "state") if run is not None else ""

    @staticmethod
    def _pr_url(agent: Any) -> str:
        for holder in (agent, getattr(agent, "target", None), getattr(agent, "pr", None)):
            if holder is None:
                continue
            url = _first_attr(holder, "pr_url", "prUrl", "url", "html_url")
            if "/pull/" in url:
                return url
        return ""

    @staticmethod
    def _startup_error(exc: Exception, context: str) -> AgentError:
        """Keep Cursor's retry hints, which decide whether a retry is safe."""
        retryable = bool(getattr(exc, "is_retryable", False))
        retry_after = getattr(exc, "retry_after", None)
        message = getattr(exc, "message", None) or str(exc)
        return AgentStartupError(
            f"{context}: {message}",
            retryable=retryable,
            retry_after=retry_after,
        )

    @staticmethod
    def _quiet_cancel(agent: Any) -> None:
        for name in ("cancel", "stop", "close"):
            method = getattr(agent, name, None)
            if callable(method):
                try:
                    method()
                except Exception:  # noqa: BLE001
                    log.debug("Could not clean up a half-started agent", exc_info=True)
                return
