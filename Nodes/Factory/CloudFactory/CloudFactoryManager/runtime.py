"""Cursor's cloud runtime: an agent on someone else's machine.

The same three verbs MachineFactory answers with Docker — see
``megadesk_contracts.factory`` — answered here with the Cursor SDK. The
difference from a local run is one keyword: Cursor clones the repository onto its
own VM, works there, pushes a branch, and opens a pull request, so the input is a
URL and the output is a link. There is no worktree to hand over and nothing for
MergeManager to merge afterwards.

Two consequences shape everything below:

* **The agent sees the pushed remote, not your working tree.** Uncommitted work
  is invisible to it, which is why the manager never sends a local path.
* **The run outlives this process.** ``bc-`` ids are durable, so a restarted BE
  reattaches by id rather than losing track of a running agent. The local factory
  has the opposite problem: a container nobody is watching is a container nobody
  reaps.

We drive the SDK through ``AsyncClient.launch_bridge`` on a dedicated event-loop
thread. The sync ``Agent.create`` path launches the bridge by ``select()``-ing
its stderr pipe, which raises ``WinError 10038`` on Windows (pipes are not
sockets). CodeScope already takes this path for the same reason.

``cursor_sdk`` is imported lazily so the FE, the wire tests, and the manager's own
logic can all import this module without the SDK installed.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
from typing import Any, Mapping, Optional

from megadesk_contracts import (
    AgentError,
    AgentRunError,
    AgentStartupError,
    RunHandle,
    RunStatus,
    repo_name_from_url,
)
from megadesk_contracts.wire import cloud as wire
from megadesk_contracts.wire.factory import normalize_status

log = logging.getLogger("cloud_factory.runtime")

DEFAULT_MODEL = wire.DEFAULT_MODEL
# Same branch MachineFactory tickets fork from and MergeManager merges into.
# Cursor cannot always read GitHub's default branch; omitting startingRef is
# what logged as [validation_error] Failed to determine repository default branch.
DEFAULT_REF = "agents"

# Cloud agents run unattended, so the prompt has to carry what a person would
# otherwise supply in review: stay small, and leave the branch in a state a
# reviewer can read in one sitting.
CLOUD_PROMPT = """{instructions}

Keep this change small and self-contained. Touch only what the task needs, do \
not reformat unrelated code, and do not add dependencies. Write a commit message \
that says what changed and why, titled: {title}"""


def prompt_for(*, instructions: str, title: str) -> str:
    return CLOUD_PROMPT.format(instructions=instructions.strip(), title=title.strip())


_GITHUB_HTTPS = re.compile(
    r"^https?://(?:www\.)?github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$",
    re.IGNORECASE,
)
_GITHUB_SSH = re.compile(
    r"^git@github\.com:([^/]+)/([^/]+?)(?:\.git)?/?$",
    re.IGNORECASE,
)
_GITHUB_SLUG = re.compile(r"^([\w.-]+)/([\w.-]+)$")


def canonical_github_repo(repo_url: str) -> tuple[str, str]:
    """``(clone_url, name)``. ``name`` is the repo, never ``owner/name``.

    Cursor's validation errors print GitHub's nameWithOwner
    (``SamJBoyer/SMOKETESTREPO``). MegaDesk, Floor, and WORKORDER identify the
    same repo as ``SMOKETESTREPO`` — the last path segment.
    """
    text = str(repo_url).strip()
    match = _GITHUB_HTTPS.match(text) or _GITHUB_SSH.match(text)
    if match is None and "://" not in text and not text.startswith("git@"):
        match = _GITHUB_SLUG.match(text)
    if match is not None:
        owner, repo = match.group(1), match.group(2)
        if repo.endswith(".git"):
            repo = repo[: -len(".git")]
        return f"https://github.com/{owner}/{repo}", repo
    return text, repo_name_from_url(text)


def cloud_launch_options(
    *, repo_url: str, auto_pr: bool = True, ref: str = ""
) -> dict[str, Any]:
    """Kwargs for ``CloudAgentOptions``. ``repos`` must be ``{url, ...}`` mappings.

    ``CloudAgentOptions.to_json()`` calls ``dict(repo)`` on each entry. A bare
    URL string is a sequence of characters, which raises
    ``dictionary update sequence element #0 has length 1; 2 is required``.
    ``ref`` belongs on the repo as ``startingRef``, not on the options object.
    An empty ``ref`` is ``agents``: MegaDesk's working branch, and the value
    Cursor needs when it cannot determine GitHub's default.
    """
    url, _name = canonical_github_repo(repo_url)
    repo: dict[str, Any] = {
        "url": url,
        "startingRef": str(ref).strip() or DEFAULT_REF,
    }
    return {
        "repos": [repo],
        # Unattended runs must not page a human, and a run with no PR is a
        # branch nobody will ever find again.
        "auto_create_pr": bool(auto_pr),
        "skip_reviewer_request": True,
    }


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


class CursorCloudFactory:
    """Launch and follow Cursor cloud agents."""

    def __init__(
        self, *, api_key: Optional[str] = None, model: str = DEFAULT_MODEL
    ) -> None:
        self.api_key = (
            api_key if api_key is not None else os.environ.get("CURSOR_API_KEY")
        )
        self.model = (model or DEFAULT_MODEL).strip() or DEFAULT_MODEL
        self._client: Any = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None

    # --- launching ---

    def launch(self, order: Mapping[str, Any]) -> RunHandle:
        """Start a cloud agent for one parsed CLOUDORDER.

        Cursor mints the id, so unlike the machine factory there is nothing to
        register until this returns — which is why ``order`` carries no
        ``run_key`` and the manager writes CLOUDRUN afterwards.
        """
        repo_url = str(order["repo_url"])
        instructions = str(order["instructions"])
        title = str(order["title"])
        auto_pr = bool(order.get("auto_pr", True))
        ref = str(order.get("ref") or "")

        url, name = canonical_github_repo(repo_url)
        CloudAgentOptions = self._options_cls()
        options = cloud_launch_options(
            repo_url=url, auto_pr=auto_pr, ref=ref
        )

        chosen = str(order.get("model") or self.model).strip() or DEFAULT_MODEL
        log.info(
            "Launching cloud agent model=%s name=%s url=%s ref=%s",
            chosen,
            name,
            url,
            options["repos"][0]["startingRef"],
        )
        try:
            return self._run(
                self._async_launch(
                    model=chosen,
                    cloud=CloudAgentOptions(**options),
                    instructions=instructions,
                    title=title,
                )
            )
        except AgentError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise self._startup_error(exc, "cloud agent could not be created") from exc

    async def _async_launch(
        self,
        *,
        model: str,
        cloud: Any,
        instructions: str,
        title: str,
    ) -> RunHandle:
        client = await self._ensure_client()
        try:
            agent = await client.agents.create(
                model=model,
                api_key=self.api_key,
                # Always explicit: with neither runtime set the SDK quietly runs
                # locally, which would run a "cloud" job on this machine.
                cloud=cloud,
            )
        except Exception as exc:  # noqa: BLE001
            raise self._startup_error(exc, "cloud agent could not be created") from exc

        agent_id = _first_attr(agent, "agent_id", "agentId", "id")
        if not agent_id:
            raise AgentStartupError("Cursor returned an agent with no id")

        try:
            run = await agent.send(prompt_for(instructions=instructions, title=title))
        except Exception as exc:  # noqa: BLE001
            # The agent exists but has no work: cancel it rather than leaving a
            # billable idle VM behind.
            await self._quiet_cancel(agent)
            raise self._startup_error(exc, f"agent {agent_id} would not accept work")

        run_id = _first_attr(run, "id", "run_id", "runId")
        log.info("Cloud agent %s running (run=%s)", agent_id, run_id or "?")
        return RunHandle(run_key=agent_id, run_id=run_id)

    # --- following ---

    def poll(self, run_key: str) -> RunStatus:
        """Where the run is now. Reattaches by id, so a restart loses nothing."""
        try:
            agent = self._run(self._async_get(run_key))
        except Exception as exc:  # noqa: BLE001
            raise AgentRunError(f"Could not read agent {run_key}: {exc}") from exc
        status = normalize_status(
            _first_attr(agent, "status", "state") or self._run_status(agent)
        )
        pr_url = self._pr_url(agent)
        if pr_url and status == wire.STATUS_RUNNING:
            # A PR exists, so the work landed; some versions report the agent as
            # running until its VM is reaped.
            status = wire.STATUS_FINISHED
        return RunStatus(
            status=status,
            result=pr_url,
            detail=_first_attr(agent, "error", "message"),
        )

    def cancel(self, run_key: str) -> None:
        try:
            self._run(self._async_cancel(run_key))
        except AgentError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise AgentRunError(f"Cursor agent {run_key} cannot be cancelled") from exc

    async def _async_get(self, agent_id: str) -> Any:
        client = await self._ensure_client()
        return await client.agents.get(agent_id, api_key=self.api_key)

    async def _async_cancel(self, run_key: str) -> None:
        client = await self._ensure_client()
        agent = await client.agents.resume(
            run_key, {"api_key": self.api_key} if self.api_key else None
        )
        for name in ("cancel", "stop", "close"):
            method = getattr(agent, name, None)
            if callable(method):
                result = method()
                if asyncio.iscoroutine(result):
                    await result
                log.info("Cancelled cloud agent %s via %s()", run_key, name)
                return
        raise AgentRunError(f"Cursor agent {run_key} cannot be cancelled")

    # --- models ---

    def models(self) -> list[str]:
        """Model ids the account can use, for the CLI.

        Empty on any failure: an unreachable model list is a cosmetic problem,
        and hardcoding ids is how a combo ends up offering models that were
        retired months ago.
        """
        try:
            return self._run(self._async_models())
        except Exception:  # noqa: BLE001
            log.debug("Could not list models", exc_info=True)
            return []

    async def _async_models(self) -> list[str]:
        client = await self._ensure_client()
        listed = await client.models.list(api_key=self.api_key)
        raw = getattr(listed, "models", None) or listed
        names: list[str] = []
        for item in raw or []:
            name = item if isinstance(item, str) else _first_attr(item, "id", "name")
            if name:
                names.append(str(name))
        return names

    # --- plumbing ---

    def _options_cls(self) -> Any:
        try:
            from cursor_sdk import CloudAgentOptions
        except ImportError as exc:
            raise AgentStartupError(
                "cursor-sdk is not installed in this environment"
            ) from exc
        return CloudAgentOptions

    async def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from cursor_sdk.asyncio import AsyncClient
        except ImportError as exc:
            raise AgentStartupError(
                "cursor-sdk is not installed in this environment"
            ) from exc
        self._client = await AsyncClient.launch_bridge(workspace=os.getcwd())
        return self._client

    def _ensure_loop(self) -> None:
        if self._loop is not None:
            return
        loop = asyncio.new_event_loop()
        ready = threading.Event()

        def _run_forever() -> None:
            asyncio.set_event_loop(loop)
            ready.set()
            loop.run_forever()

        thread = threading.Thread(
            target=_run_forever,
            name="cloud-factory-sdk",
            daemon=True,
        )
        thread.start()
        if not ready.wait(timeout=5):
            raise AgentStartupError("SDK event loop failed to start")
        self._loop = loop
        self._loop_thread = thread

    def _run(self, coro: Any) -> Any:
        self._ensure_loop()
        assert self._loop is not None
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def close(self) -> None:
        client = self._client
        self._client = None

        async def _shutdown() -> None:
            if client is None:
                return
            closer = getattr(client, "aclose", None) or getattr(client, "close", None)
            if callable(closer):
                result = closer()
                if asyncio.iscoroutine(result):
                    await result

        try:
            if self._loop is not None and client is not None:
                self._run(_shutdown())
        except Exception:  # noqa: BLE001
            log.debug("SDK client close failed", exc_info=True)
        loop = self._loop
        thread = self._loop_thread
        self._loop = None
        self._loop_thread = None
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(loop.stop)
        except Exception:  # noqa: BLE001
            pass
        if thread is not None:
            thread.join(timeout=5)
        try:
            loop.close()
        except Exception:  # noqa: BLE001
            pass

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
    async def _quiet_cancel(agent: Any) -> None:
        for name in ("cancel", "stop", "close"):
            method = getattr(agent, name, None)
            if callable(method):
                try:
                    result = method()
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:  # noqa: BLE001
                    log.debug("Could not clean up a half-started agent", exc_info=True)
                return
