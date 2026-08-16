"""One warm Cursor agent per clone, streaming its answer back as text.

This is the whole retrieval layer. A local Cursor agent bound to ``cwd`` already
has search and file-read tools over that directory, so there is no index to
build, no embeddings to store, and no cloud pipeline to keep in sync — the agent
looks things up the way a person would. Keeping one ``Agent`` alive per session
is what makes follow-up questions cheap and gives them conversation memory.

We drive the SDK through ``AsyncClient.launch_bridge`` on a dedicated event-loop
thread. The sync ``Agent.create`` path launches the bridge by ``select()``-ing
its stderr pipe, which raises ``WinError 10038`` on Windows (pipes are not
sockets). The async bridge avoids that, and is the documented shape for
long-lived services anyway.

``cursor_sdk`` is imported lazily so the manager, its tests, and the FE can all
import this module without the SDK installed.
"""

from __future__ import annotations

import asyncio
import logging
import os
import queue
import threading
from pathlib import Path
from typing import Any, Iterator, Optional

from megadesk_contracts import AgentError, AgentRunError, AgentStartupError

log = logging.getLogger("code_scope.runner")

DEFAULT_MODEL = "auto"

# Answers are spoken aloud, so markdown is noise and length is expensive. The
# read-only instruction matters because a local agent does have write tools: the
# clone is disposable, but a silent edit would still confuse the next question.
ANSWER_PROMPT = """Answer this question about the repository in the current working directory.

Read and search only. Do not create, modify, or delete files, and do not run \
commands that change state.

Your answer will be read aloud, so write plain spoken prose: no markdown, no \
bullet points, no code blocks, no file trees. Name files and symbols in words. \
Be specific about where things live. Keep it under six sentences. If the answer \
is not in this repository, say so plainly instead of guessing.

Question: {question}"""

PROPOSE_TICKET_PROMPT = """Turn this request into a work order for another agent, \
based on the repository in the current working directory.

Read and search only; make no changes yourself.

Reply with a single title line, then a blank line, then the instructions. The \
title must be under ten words. The instructions must name the specific files to \
change and say what the result should look like, so an agent with no memory of \
this conversation can act on them alone. Write prose, not markdown.

Request: {request}"""


def prompt_for(question: str, *, mode: str) -> str:
    from megadesk_contracts.wire import code_scope as wire

    if mode == wire.MODE_PROPOSE_TICKET:
        return PROPOSE_TICKET_PROMPT.format(request=question)
    return ANSWER_PROMPT.format(question=question)


class CursorRunner:
    """A durable local Cursor agent bound to one clone.

    Resumes rather than recreates when handed an ``agent_id``, so a restarted BE
    picks the conversation back up instead of starting cold.

    The manager's Redis loop is sync, so this class keeps a private asyncio loop
    on a daemon thread and exposes the same sync ``open`` / ``answer`` / ``close``
    surface as before.
    """

    def __init__(
        self,
        *,
        cwd: Path,
        model: str = DEFAULT_MODEL,
        api_key: Optional[str] = None,
        agent_id: str = "",
    ) -> None:
        self.cwd = Path(cwd)
        self.model = (model or DEFAULT_MODEL).strip() or DEFAULT_MODEL
        self.api_key = (
            api_key if api_key is not None else os.environ.get("CURSOR_API_KEY")
        )
        self.agent_id = (agent_id or "").strip()
        self._agent: Any = None
        self._client: Any = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None

    @property
    def is_open(self) -> bool:
        return self._agent is not None

    # --- lifecycle ---

    def open(self) -> None:
        """Create or resume the agent. Idempotent."""
        if self._agent is not None:
            return
        if not self.cwd.is_dir():
            raise AgentStartupError(f"Clone directory does not exist: {self.cwd}")

        try:
            from cursor_sdk import AgentOptions, LocalAgentOptions
            from cursor_sdk.asyncio import AsyncClient
        except ImportError as exc:
            raise AgentStartupError(
                "cursor-sdk is not installed in this environment"
            ) from exc

        try:
            self._ensure_loop()
            self._client, self._agent = self._run(
                self._boot(AsyncClient, AgentOptions, LocalAgentOptions)
            )
        except Exception as exc:  # noqa: BLE001
            self._drop_handles()
            self._teardown_loop()
            raise self._classify(exc, "agent could not be started") from exc

        found = getattr(self._agent, "agent_id", None) or getattr(
            self._agent, "agentId", None
        )
        if found:
            self.agent_id = str(found)
        log.info("Agent ready id=%s", self.agent_id or "(unknown)")

    async def _boot(
        self,
        AsyncClient: Any,
        AgentOptions: Any,
        LocalAgentOptions: Any,
    ) -> tuple[Any, Any]:
        # Always pass ``local`` explicitly: the SDK defaults to local when
        # neither runtime is set, and a silent default is a trap worth spending
        # one line to avoid.
        client = await AsyncClient.launch_bridge(workspace=str(self.cwd))
        try:
            if self.agent_id:
                log.info("Resuming agent id=%s cwd=%s", self.agent_id, self.cwd)
                agent = await client.agents.resume(
                    self.agent_id,
                    AgentOptions(api_key=self.api_key),
                )
            else:
                log.info("Creating agent model=%s cwd=%s", self.model, self.cwd)
                agent = await client.agents.create(
                    model=self.model,
                    api_key=self.api_key,
                    local=LocalAgentOptions(cwd=str(self.cwd)),
                )
        except Exception:
            await client.aclose()
            raise
        return client, agent

    def close(self) -> None:
        agent = self._agent
        client = self._client
        self._agent = None
        self._client = None
        if agent is None and client is None:
            self._teardown_loop()
            return

        async def _shutdown() -> None:
            if agent is not None:
                try:
                    await agent.close()
                except Exception:  # noqa: BLE001 - disposal must not mask a real error
                    log.warning("Agent close failed for %s", self.cwd, exc_info=True)
            if client is not None:
                try:
                    await client.aclose()
                except Exception:  # noqa: BLE001
                    log.warning("SDK client close failed for %s", self.cwd, exc_info=True)

        try:
            if self._loop is not None:
                self._run(_shutdown())
        except Exception:  # noqa: BLE001
            log.warning("Agent shutdown failed for %s", self.cwd, exc_info=True)
        finally:
            self._teardown_loop()

    # --- work ---

    def answer(self, question: str, *, mode: str) -> Iterator[str]:
        """Yield the agent's answer text as it arrives."""
        self.open()
        prompt = prompt_for(question, mode=mode)
        out: queue.Queue[tuple[str, Any]] = queue.Queue()

        async def _produce() -> None:
            try:
                run = await self._agent.send(prompt)
            except Exception as exc:  # noqa: BLE001
                out.put(("err", self._classify(exc, "send failed")))
                return

            run_id = getattr(run, "id", None)
            log.info("Run started id=%s agent=%s", run_id, self.agent_id or "(unknown)")

            produced = False
            try:
                async for chunk in run.iter_text():
                    text = str(chunk or "")
                    if text:
                        produced = True
                        out.put(("chunk", text))
                result = await run.wait()
            except Exception as exc:  # noqa: BLE001
                out.put(("err", AgentRunError(f"Run {run_id} failed: {exc}")))
                return

            status = str(getattr(result, "status", "") or "")
            if status and status != "finished":
                out.put(
                    ("err", AgentRunError(f"Run {run_id} ended with status={status}"))
                )
                return

            if not produced:
                text = str(getattr(result, "result", "") or "").strip()
                if text:
                    out.put(("chunk", text))
            out.put(("done", None))

        assert self._loop is not None
        fut = asyncio.run_coroutine_threadsafe(_produce(), self._loop)
        error: BaseException | None = None
        try:
            while True:
                kind, payload = out.get()
                if kind == "chunk":
                    yield payload
                elif kind == "done":
                    break
                elif kind == "err":
                    error = payload
                    break
                else:  # pragma: no cover - defensive
                    error = AgentRunError(f"Unexpected runner event: {kind}")
                    break
        finally:
            # Wait so a cancelled consumer cannot leave a run watcher dangling.
            try:
                fut.result(timeout=60)
            except Exception as exc:  # noqa: BLE001
                if not fut.done():
                    fut.cancel()
                if error is None:
                    error = AgentRunError(f"Run failed: {exc}")
        if error is not None:
            raise error

    # --- event loop ---

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
            name=f"code-scope-sdk-{self.cwd.name}",
            daemon=True,
        )
        thread.start()
        if not ready.wait(timeout=5):
            raise AgentStartupError("SDK event loop failed to start")
        self._loop = loop
        self._loop_thread = thread

    def _run(self, coro: Any) -> Any:
        assert self._loop is not None
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def _drop_handles(self) -> None:
        self._agent = None
        self._client = None

    def _teardown_loop(self) -> None:
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

    def _classify(self, exc: Exception, context: str) -> AgentError:
        """Map an SDK exception onto the startup / run distinction."""
        try:
            from cursor_sdk import CursorAgentError
        except ImportError:
            CursorAgentError = ()  # type: ignore[misc, assignment]
        if isinstance(exc, CursorAgentError) or isinstance(exc, (OSError, ValueError)):
            retryable = getattr(exc, "is_retryable", None)
            suffix = "" if retryable is None else f" (retryable={bool(retryable)})"
            return AgentStartupError(f"{context}: {exc}{suffix}")
        return AgentRunError(f"{context}: {exc}")
