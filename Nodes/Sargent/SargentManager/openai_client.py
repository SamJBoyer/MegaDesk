"""One Chat Completions call. No SDK — urllib is already enough."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Optional

CHAT_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_MODEL = "gpt-4o"
DEFAULT_TIMEOUT_SEC = 60.0

SYSTEM = (
    "You rewrite prompts. The user will paste a rough human request. "
    "Return a clearer, better-structured version of that same request. "
    "Fix spelling and grammar, add missing structure, and keep the original "
    "intent. Do not answer the request. Do not add a preamble or quotes. "
    "Return only the improved prompt."
)


class OpenAIError(RuntimeError):
    """The Chat Completions call could not produce a rewrite."""


def resolve_api_key(api_key: Optional[str] = None) -> str:
    return (api_key if api_key is not None else os.environ.get("OPENAI_API_KEY") or "").strip()


def resolve_model(model: Optional[str] = None) -> str:
    return (model or os.environ.get("SARGENT_MODEL") or DEFAULT_MODEL).strip() or DEFAULT_MODEL


def rewrite_prompt(
    prompt: str,
    *,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT_SEC,
) -> str:
    """Return the improved prompt, or raise ``OpenAIError``."""
    key = resolve_api_key(api_key)
    if not key:
        raise OpenAIError("OPENAI_API_KEY is not set")

    body = json.dumps(
        {
            "model": resolve_model(model),
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        CHAT_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:240]
        raise OpenAIError(f"OpenAI HTTP {exc.code}: {detail or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise OpenAIError(f"OpenAI request failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise OpenAIError("OpenAI request timed out") from exc
    except json.JSONDecodeError as exc:
        raise OpenAIError("OpenAI returned unreadable JSON") from exc

    try:
        text = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise OpenAIError("OpenAI response had no rewrite text") from exc
    rewritten = str(text or "").strip()
    if not rewritten:
        raise OpenAIError("OpenAI returned an empty rewrite")
    return rewritten
