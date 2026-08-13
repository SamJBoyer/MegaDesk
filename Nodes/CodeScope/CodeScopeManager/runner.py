"""One warm Cursor agent per clone, streaming its answer back as text.

This is the whole retrieval layer. A local Cursor agent bound to ``cwd`` already
has search and file-read tools over that directory, so there is no index to
build, no embeddings to store, and no cloud pipeline to keep in sync — the agent
looks things up the way a person would. Keeping one ``Agent`` alive per session
is what makes follow-up questions cheap and gives them conversation memory.

``cursor_sdk`` is imported lazily so the manager, its tests, and the FE can all
import this module without the SDK installed.
"""

from __future__ import annotations

import logging
import os
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
            from cursor_sdk import Agent, LocalAgentOptions
        except ImportError as exc:
            raise AgentStartupError(
                "cursor-sdk is not installed in this environment"
            ) from exc

        try:
            if self.agent_id:
                log.info("Resuming agent id=%s cwd=%s", self.agent_id, self.cwd)
                self._agent = Agent.resume(self.agent_id, api_key=self.api_key)
            else:
                log.info("Creating agent model=%s cwd=%s", self.model, self.cwd)
                # Always pass ``local`` explicitly: the SDK defaults to local
                # when neither runtime is set, and a silent default is a trap
                # worth spending one line to avoid.
                self._agent = Agent.create(
                    model=self.model,
                    api_key=self.api_key,
                    local=LocalAgentOptions(cwd=str(self.cwd)),
                )
        except Exception as exc:  # noqa: BLE001
            raise self._classify(exc, "agent could not be started") from exc

        found = getattr(self._agent, "agent_id", None) or getattr(
            self._agent, "agentId", None
        )
        if found:
            self.agent_id = str(found)
        log.info("Agent ready id=%s", self.agent_id or "(unknown)")

    def close(self) -> None:
        agent = self._agent
        self._agent = None
        if agent is None:
            return
        try:
            agent.close()
        except Exception:  # noqa: BLE001 - disposal must not mask a real error
            log.warning("Agent close failed for %s", self.cwd, exc_info=True)

    # --- work ---

    def answer(self, question: str, *, mode: str) -> Iterator[str]:
        """Yield the agent's answer text as it arrives."""
        self.open()
        prompt = prompt_for(question, mode=mode)

        try:
            run = self._agent.send(prompt)
        except Exception as exc:  # noqa: BLE001
            raise self._classify(exc, "send failed") from exc

        run_id = getattr(run, "id", None)
        log.info("Run started id=%s agent=%s", run_id, self.agent_id or "(unknown)")

        produced = False
        try:
            for chunk in self._stream(run):
                if chunk:
                    produced = True
                    yield chunk
            result = run.wait()
        except AgentError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise AgentRunError(f"Run {run_id} failed: {exc}") from exc

        status = str(getattr(result, "status", "") or "")
        if status and status != "finished":
            raise AgentRunError(f"Run {run_id} ended with status={status}")

        if not produced:
            text = str(getattr(result, "result", "") or "").strip()
            if text:
                yield text

    def _stream(self, run: Any) -> Iterator[str]:
        """Prefer text streaming; fall back to the terminal result.

        ``wait()`` is called by the caller either way — it is how you learn
        whether the run finished, and skipping it leaks the run's watchers.
        """
        iter_text = getattr(run, "iter_text", None)
        if callable(iter_text):
            for chunk in iter_text():
                yield str(chunk or "")
            return
        return

    def _classify(self, exc: Exception, context: str) -> AgentError:
        """Map an SDK exception onto the startup / run distinction."""
        name = type(exc).__name__
        if name == "CursorAgentError" or isinstance(exc, (OSError, ValueError)):
            retryable = getattr(exc, "is_retryable", None)
            suffix = "" if retryable is None else f" (retryable={bool(retryable)})"
            return AgentStartupError(f"{context}: {exc}{suffix}")
        return AgentRunError(f"{context}: {exc}")
