"""What the voice model is allowed to do, and how it is told to behave.

The instructions carry more weight than usual here because of one timing fact:
``ask_codebase`` cannot return an answer. A Cursor agent takes seconds to a
minute, and a realtime tool result is expected in well under a second, so the
tool returns ``searching`` and the real answer arrives later as a separate
message. The model has to be told that, or it will either invent an answer or go
silent — both of which read as broken.

``end_session`` is the other trap. Server VAD already treats a pause as the end
of a turn, and the model will happily map that onto hanging up unless it is told
— and the backend enforces — that only an explicit goodbye closes the socket.
"""

from __future__ import annotations

import re

TOOL_ASK_CODEBASE = "ask_codebase"
TOOL_DISPATCH_DOC_AGENT = "dispatch_doc_agent"
TOOL_SET_REPO = "set_repo"
TOOL_CREATE_NOTE = "create_note"
TOOL_ADD_NOTE_TEXT = "add_note_text"
TOOL_SWITCH_NOTE = "switch_note"
TOOL_END_SESSION = "end_session"

# Prefix on the injected conversation item, so the model can tell a retrieved
# answer apart from something the user said.
ANSWER_PREFIX = "[codebase]"

_FAREWELL_RE = re.compile(
    r"\b("
    r"good\s*bye|bye(?:-bye)?"
    r"|that['’]s\s+all|thats\s+all|that\s+is\s+all"
    r"|that['’]s\s+it|thats\s+it"
    r"|hang\s*up"
    r"|end(?:\s+the)?\s+session"
    r"|stop\s+listening"
    r"|i(?:['’]m|\s+am)\s+done"
    r"|we(?:['’]re|\s+are)\s+done"
    r"|i(?:['’]m|\s+am)\s+finished"
    r"|we(?:['’]re|\s+are)\s+finished"
    r")\b",
    re.IGNORECASE,
)

INSTRUCTIONS = f"""You are a voice assistant for a software developer, talking about \
their code out loud.

Ground every claim about the code in a tool call. You cannot see the repository \
yourself, so never guess at file names, function names, or behaviour: call \
{TOOL_ASK_CODEBASE} and wait.

{TOOL_ASK_CODEBASE} returns immediately with status "searching". That is not the \
answer. When it does, say one short thing to hold the floor — "let me look" — and \
then wait silently. Do not call {TOOL_END_SESSION}. The session stays open. The \
answer arrives moments later as a message starting with "{ANSWER_PREFIX}". Relay \
that message in your own words, briefly, as if you had just read it. Never repeat \
the "{ANSWER_PREFIX}" marker out loud. After relaying it, keep listening for a \
follow-up.

A pause after the user speaks is the start of your turn, not the end of the \
session. Call {TOOL_END_SESSION} only after the user explicitly says they are \
finished — goodbye, that's all, hang up. Never call it after a question, after \
another tool, or while waiting for a codebase answer.

Before calling {TOOL_DISPATCH_DOC_AGENT}, say the title and the gist of the \
instructions back to the user and wait for them to agree. Dispatching sends an \
agent to write code and open a pull request, so it is not something to do on a \
guess.

When the user asks you to write something down, use {TOOL_CREATE_NOTE}, \
{TOOL_ADD_NOTE_TEXT}, and {TOOL_SWITCH_NOTE}. Those tools update the notepad \
on the canvas. They return immediately.

Keep every reply to one or two spoken sentences. No markdown, no lists, no code \
read aloud character by character. If you do not know, say so."""


def is_farewell(text: str) -> bool:
    """True when the user asked to hang up, not merely finished a turn."""
    return bool(_FAREWELL_RE.search((text or "").strip()))


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
            "name": TOOL_CREATE_NOTE,
            "description": (
                "Create a notepad document and make it the current target. "
                "Optional text becomes the starting body."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Document name, used as the tab and the .txt file.",
                    },
                    "text": {
                        "type": "string",
                        "description": "Optional starting text.",
                    },
                },
                "required": ["title"],
            },
        },
        {
            "type": "function",
            "name": TOOL_ADD_NOTE_TEXT,
            "description": (
                "Append text to a notepad document. Omitting title writes to "
                "the current target."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Text to add.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Document to write to. Defaults to the current one.",
                    },
                },
                "required": ["text"],
            },
        },
        {
            "type": "function",
            "name": TOOL_SWITCH_NOTE,
            "description": "Switch which notepad document later additions go to.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Document name to make current.",
                    }
                },
                "required": ["title"],
            },
        },
        {
            "type": "function",
            "name": TOOL_END_SESSION,
            "description": (
                "Hang up only after the user explicitly says they are finished "
                "(goodbye, that's all, hang up). A pause is not a goodbye. "
                "Never call this after a question or while waiting for a "
                "codebase answer."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    ]
