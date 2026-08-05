"""Term autocomplete for text editing surfaces."""

from __future__ import annotations

from typing import Iterable, Optional


def term_names(terms: Iterable[dict]) -> list[str]:
    """Collect non-empty term strings from the canvas glossary."""
    names: list[str] = []
    seen: set[str] = set()
    for entry in terms:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("term", "")).strip()
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


def split_trailing_word(text: str) -> tuple[str, str]:
    """Split *text* into (prefix_including_whitespace, trailing_word)."""
    if not text:
        return "", ""
    i = len(text) - 1
    while i >= 0 and not text[i].isspace():
        i -= 1
    return text[: i + 1], text[i + 1 :]


def match_terms(
    text: str,
    terms: Iterable[dict],
    *,
    limit: int = 6,
) -> list[str]:
    """Return glossary terms that the trailing word is a strict prefix of.

    Matching is case-insensitive. Exact full matches are excluded (nothing left
    to complete). Empty / single-character prefixes are ignored until the user
    has typed at least 1 character that could narrow a term.
    """
    _, word = split_trailing_word(text)
    if not word:
        return []

    needle = word.casefold()
    matches: list[str] = []
    for name in term_names(terms):
        folded = name.casefold()
        if folded == needle:
            continue
        if folded.startswith(needle):
            matches.append(name)
    matches.sort(key=lambda n: (len(n), n.casefold()))
    return matches[:limit]


def apply_completion(text: str, term: str) -> str:
    """Replace the trailing word in *text* with *term*."""
    head, _ = split_trailing_word(text)
    return head + term


def best_match(text: str, terms: Iterable[dict]) -> Optional[str]:
    matches = match_terms(text, terms, limit=1)
    return matches[0] if matches else None
