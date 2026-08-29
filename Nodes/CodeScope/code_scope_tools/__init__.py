"""Voice tools for CodeScope: ask about a clone, switch it, or change it."""

from __future__ import annotations

from typing import Any

from megadesk_contracts import ToolSpec
from megadesk_contracts.repo import CloneError
from megadesk_contracts.wire import cloud as cloud_wire
from megadesk_contracts.wire import code_scope as scope_wire
from megadesk_contracts.wire import voice as voice_wire

NODE_NAME = "code_scope"
TOOL_ASK_CODEBASE = "ask_codebase"
TOOL_DISPATCH_DOC_AGENT = "dispatch_doc_agent"
TOOL_SET_REPO = "set_repo"

# Prefix on the injected conversation item, so the model can tell a retrieved
# answer apart from something the user said.
ANSWER_PREFIX = "[codebase]"

INSTRUCTIONS = f"""Ground every claim about the code in a tool call. You cannot \
see the repository yourself, so never guess at file names, function names, or \
behaviour: call {TOOL_ASK_CODEBASE} and wait.

{TOOL_ASK_CODEBASE} returns immediately with status "searching". That is not the \
answer. When it does, say one short thing to hold the floor — "let me look" — and \
then wait silently. Do not call end_session. The session stays open. The answer \
arrives moments later as a message starting with "{ANSWER_PREFIX}". Relay that \
message in your own words, briefly, as if you had just read it. Never repeat the \
"{ANSWER_PREFIX}" marker out loud. After relaying it, keep listening for a \
follow-up.

Before calling {TOOL_DISPATCH_DOC_AGENT}, say the title and the gist of the \
instructions back to the user and wait for them to agree. Dispatching sends an \
agent to write code and open a pull request, so it is not something to do on a \
guess. {TOOL_SET_REPO} switches which loaded repository questions are about."""


def handle_ask_codebase(arguments: dict, host: Any) -> dict:
    question = str(arguments.get("question") or "").strip()
    if not question:
        return {"status": "error", "detail": "no question was provided"}

    resolved = host.resolve_scope_session(host.target_repo)
    if resolved is None:
        detail = "no repository is loaded in CodeScope"
        host.publish(voice_wire.KIND_ERROR, detail)
        return {"status": "error", "detail": detail}

    scope_session_id, repo = resolved
    question_id = scope_wire.new_question_id()
    host.ephemeral.xadd(
        scope_wire.ASK_STREAM,
        scope_wire.ask_fields(
            session_id=scope_session_id,
            question_id=question_id,
            repo=repo,
            question=question,
        ),
    )
    host.remember_question(question_id, host.current_call_id)
    host.set_state(voice_wire.STATE_THINKING)
    return {
        "status": "searching",
        "detail": (
            "The answer will arrive shortly as a message beginning with "
            f"{ANSWER_PREFIX}. Say one short thing, then wait silently. "
            "Do not call end_session; the session stays open."
        ),
    }


def handle_dispatch_doc_agent(arguments: dict, host: Any) -> dict:
    instructions = str(arguments.get("instructions") or "").strip()
    if not instructions:
        return {"status": "error", "detail": "no instructions were provided"}
    title = str(arguments.get("title") or "").strip() or _title_from(instructions)
    repo = str(arguments.get("target") or "").strip() or host.target_repo

    resolved = host.resolve_scope_session(repo)
    if resolved is None:
        detail = "no repository is loaded to dispatch against"
        host.publish(voice_wire.KIND_ERROR, detail)
        return {"status": "error", "detail": detail}
    scope_session_id, repo = resolved

    try:
        url = host.repo_url(scope_session_id)
    except (CloneError, ValueError) as exc:
        detail = f"could not resolve the remote for {repo}: {exc}"
        host.publish(voice_wire.KIND_ERROR, detail)
        return {"status": "error", "detail": detail}

    order_id = cloud_wire.new_order_id()
    order = cloud_wire.cloudorder_fields(
        order_id=order_id,
        repo_url=url,
        title=title,
        instructions=instructions,
        auto_pr=True,
    )
    cloud_wire.publish_cloudorder(host.ephemeral, order)

    host.publish(voice_wire.KIND_DISPATCH, f"{cloud_wire.STATUS_QUEUED}: {title}")
    return {
        "status": cloud_wire.STATUS_QUEUED,
        "order_id": order_id,
        "title": title,
        "detail": "Queued to run.",
    }


def handle_set_repo(arguments: dict, host: Any) -> dict:
    repo = str(arguments.get("repo") or "").strip()
    resolved = host.resolve_scope_session(repo)
    if resolved is None:
        return {
            "status": "error",
            "detail": f"{repo or 'that repository'} is not loaded",
            "available": host.loaded_repos(),
        }
    _session_id, repo = resolved
    host.target_repo = repo
    host.publish(voice_wire.KIND_TARGET, repo)
    return {"status": "ok", "repo": repo}


def _title_from(instructions: str) -> str:
    words = instructions.split()
    return " ".join(words[:8]) or "documentation change"


def tool_spec() -> ToolSpec:
    return ToolSpec(
        name=NODE_NAME,
        instructions=INSTRUCTIONS,
        schemas=(
            {
                "type": "function",
                "name": TOOL_ASK_CODEBASE,
                "description": (
                    "Ask a question about the repository that is currently loaded. "
                    "Returns immediately with status 'searching'; the answer follows "
                    f"as a separate message beginning with '{ANSWER_PREFIX}'."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": (
                                "The question, self-contained. Include the context "
                                "from earlier in the conversation, since the code "
                                "agent cannot hear it."
                            ),
                        }
                    },
                    "required": ["question"],
                },
            },
            {
                "type": "function",
                "name": TOOL_DISPATCH_DOC_AGENT,
                "description": (
                    "Send a cloud agent to make a small documentation or comment "
                    "change and open a pull request. Confirm with the user first."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "Under ten words, used as the PR title.",
                        },
                        "instructions": {
                            "type": "string",
                            "description": (
                                "What to change and what the result should look "
                                "like, naming specific files. The agent has no "
                                "memory of this conversation."
                            ),
                        },
                        "target": {
                            "type": "string",
                            "description": (
                                "Repository name. Defaults to the loaded one."
                            ),
                        },
                    },
                    "required": ["title", "instructions"],
                },
            },
            {
                "type": "function",
                "name": TOOL_SET_REPO,
                "description": "Switch which loaded repository questions are about.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "repo": {
                            "type": "string",
                            "description": "Repository name.",
                        }
                    },
                    "required": ["repo"],
                },
            },
        ),
        handlers={
            TOOL_ASK_CODEBASE: handle_ask_codebase,
            TOOL_DISPATCH_DOC_AGENT: handle_dispatch_doc_agent,
            TOOL_SET_REPO: handle_set_repo,
        },
    )
