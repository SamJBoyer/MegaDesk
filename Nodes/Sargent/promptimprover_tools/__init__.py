"""Voice tools for PromptImprover: revise a spoken prompt and read the rewrite."""

from __future__ import annotations

from typing import Any

from megadesk_contracts import ToolSpec
from megadesk_contracts.wire import sargent as wire
from megadesk_contracts.wire import voice as voice_wire

NODE_NAME = "promptimprover"
TOOL_REVISE_MY_PROMPT = "revise_my_prompt"

# Prefix on the injected conversation item, so the model can tell a rewrite
# apart from something the user said.
REWRITE_PREFIX = "[revised]"

INSTRUCTIONS = f"""When the user asks you to improve, revise, or rewrite a \
prompt, call {TOOL_REVISE_MY_PROMPT} with their exact wording. Do not rewrite \
it yourself.

{TOOL_REVISE_MY_PROMPT} returns immediately with status "revising". That is not \
the rewrite. When it does, say one short thing to hold the floor — "let me \
revise that" — and then wait silently. Do not call end_session. The session \
stays open. The rewrite arrives moments later as a message starting with \
"{REWRITE_PREFIX}". Read that revised prompt out loud. Say what the revised \
prompt is. Never repeat the "{REWRITE_PREFIX}" marker out loud. After speaking \
it, keep listening for a follow-up."""


def handle_revise_my_prompt(arguments: dict, host: Any) -> dict:
    prompt = str(arguments.get("prompt") or "").strip()
    if not prompt:
        return {"status": "error", "detail": "no prompt was provided"}

    prompt_id = wire.new_prompt_id()
    host.ephemeral.xadd(
        wire.ASK_STREAM,
        wire.ask_fields(
            session_id=host.session_id,
            prompt_id=prompt_id,
            prompt=prompt,
        ),
    )
    host.remember_question(prompt_id, host.current_call_id)
    host.set_state(voice_wire.STATE_THINKING)
    return {
        "status": "revising",
        "detail": (
            "The revised prompt will arrive shortly as a message beginning with "
            f"{REWRITE_PREFIX}. Say one short thing, then wait silently. "
            "When it arrives, read the revised prompt out loud. "
            "Do not call end_session; the session stays open."
        ),
    }


def tool_spec() -> ToolSpec:
    return ToolSpec(
        name=NODE_NAME,
        instructions=INSTRUCTIONS,
        schemas=(
            {
                "type": "function",
                "name": TOOL_REVISE_MY_PROMPT,
                "description": (
                    "Revise My Prompt. Rewrite a rough prompt into a clearer one. "
                    "Returns immediately with status 'revising'; the rewrite "
                    f"follows as a separate message beginning with '{REWRITE_PREFIX}'. "
                    "Then read that revised prompt out loud."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": (
                                "The user's input prompt, as they said it."
                            ),
                        }
                    },
                    "required": ["prompt"],
                },
            },
        ),
        handlers={TOOL_REVISE_MY_PROMPT: handle_revise_my_prompt},
    )
