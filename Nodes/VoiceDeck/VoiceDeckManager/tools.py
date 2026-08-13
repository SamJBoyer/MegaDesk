"""What the voice model is allowed to do, and how it is told to behave.

The instructions carry more weight than usual here because of one timing fact:
``ask_codebase`` cannot return an answer. A Cursor agent takes seconds to a
minute, and a realtime tool result is expected in well under a second, so the
tool returns ``searching`` and the real answer arrives later as a separate
message. The model has to be told that, or it will either invent an answer or go
silent — both of which read as broken.
"""

from __future__ import annotations

TOOL_ASK_CODEBASE = "ask_codebase"
TOOL_DISPATCH_DOC_AGENT = "dispatch_doc_agent"
TOOL_SET_REPO = "set_repo"
TOOL_END_SESSION = "end_session"

# Prefix on the injected conversation item, so the model can tell a retrieved
# answer apart from something the user said.
ANSWER_PREFIX = "[codebase]"

INSTRUCTIONS = f"""You are a voice assistant for a software developer, talking about \
their code out loud.

Ground every claim about the code in a tool call. You cannot see the repository \
yourself, so never guess at file names, function names, or behaviour: call \
{TOOL_ASK_CODEBASE} and wait.

{TOOL_ASK_CODEBASE} returns immediately with status "searching". That is not the \
answer. When it does, say one short thing to hold the floor — "let me look" — and \
then stop talking. The answer arrives moments later as a message starting with \
"{ANSWER_PREFIX}". Relay that message in your own words, briefly, as if you had \
just read it. Never repeat the "{ANSWER_PREFIX}" marker out loud.

Before calling {TOOL_DISPATCH_DOC_AGENT}, say the title and the gist of the \
instructions back to the user and wait for them to agree. Dispatching sends an \
agent to write code and open a pull request, so it is not something to do on a \
guess.

Keep every reply to one or two spoken sentences. No markdown, no lists, no code \
read aloud character by character. If you do not know, say so."""


def tool_schemas() -> list[dict]:
    """Realtime ``session.tools`` entries."""
    return [
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
                    "repo": {"type": "string", "description": "Repository name."}
                },
                "required": ["repo"],
            },
        },
        {
            "type": "function",
            "name": TOOL_END_SESSION,
            "description": "End the voice session when the user is done talking.",
            "parameters": {"type": "object", "properties": {}},
        },
    ]
