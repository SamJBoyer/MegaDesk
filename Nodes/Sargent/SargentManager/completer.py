"""One OpenAI chat-completions call. Stdlib only — no ``openai`` package."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Callable

DEFAULT_MODEL = "gpt-4o"
CHAT_URL = "https://api.openai.com/v1/chat/completions"
SYSTEM_PROMPT = (
    "Rewrite the user's rough prompt into a clearer, better-structured prompt "
    "for another model. Fix spelling and grammar. Preserve the original intent. "
    "Do not answer the prompt. Return only the rewritten prompt."
)


class RewriteError(Exception):
    """The OpenAI call could not produce a rewrite."""


UrlOpen = Callable[..., Any]


class OpenAICompleter:
    """``completer(prompt) -> rewrite``. Swap this in tests."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        urlopen: UrlOpen | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.api_key = (
            api_key if api_key is not None else os.environ.get("OPENAI_API_KEY")
        )
        self.model = (model or os.environ.get("SARGENT_MODEL") or DEFAULT_MODEL).strip()
        self._urlopen = urlopen or urllib.request.urlopen
        self.timeout = float(timeout)

    def __call__(self, prompt: str) -> str:
        key = (self.api_key or "").strip()
        if not key:
            raise RewriteError("OPENAI_API_KEY is not set")
        body = json.dumps(
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.2,
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
            with self._urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:240]
            raise RewriteError(f"OpenAI HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RewriteError(f"OpenAI request failed: {exc.reason}") from exc

        try:
            payload = json.loads(raw.decode("utf-8"))
            text = payload["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise RewriteError("OpenAI returned an unusable completion") from exc
        rewrite = str(text or "").strip()
        if not rewrite:
            raise RewriteError("OpenAI returned an empty rewrite")
        return rewrite
