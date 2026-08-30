"""Kanban board the massive-project dispatcher writes and ralph consumes.

Cards are plain dicts so they can sit on ``WorkState`` and survive a LangGraph
merge. Priority is 1-based: 1 is the first card ralph should pick up.
"""

from __future__ import annotations

import json
import re
from typing import Any

CARD_TODO = "todo"
CARD_DONE = "done"
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def empty_card(
    *,
    card_id: str,
    title: str,
    detail: str = "",
    priority: int = 1,
) -> dict[str, Any]:
    return {
        "id": str(card_id),
        "title": str(title).strip(),
        "detail": str(detail).strip(),
        "priority": int(priority),
        "status": CARD_TODO,
        "commit_sha": "",
    }


def parse_kanban(text: str) -> list[dict[str, Any]]:
    """Read a card list from an agent reply.

    Accepts a JSON array, ``{"cards": [...]}``, or the same wrapped in a
    fenced code block. Extra prose around the JSON is ignored.
    """
    blob = _json_blob(text)
    if blob is None:
        raise ValueError("dispatcher reply had no JSON kanban")
    raw = json.loads(blob)
    items: list[Any]
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        items = raw.get("cards") or raw.get("kanban") or raw.get("items") or []
    else:
        raise ValueError("kanban JSON must be an array or an object with cards")
    if not isinstance(items, list) or not items:
        raise ValueError("kanban has no cards")

    cards: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"kanban card {index} is not an object")
        title = str(item.get("title") or item.get("name") or "").strip()
        if not title:
            raise ValueError(f"kanban card {index} has no title")
        try:
            priority = int(item.get("priority") or index)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"kanban card {index} has a bad priority") from exc
        cards.append(
            empty_card(
                card_id=str(item.get("id") or index),
                title=title,
                detail=str(item.get("detail") or item.get("body") or ""),
                priority=priority,
            )
        )
    return rank_cards(cards)


def rank_cards(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Lowest priority number first, then original order."""
    decorated = list(enumerate(cards))
    decorated.sort(key=lambda pair: (int(pair[1].get("priority") or 0), pair[0]))
    return [card for _index, card in decorated]


def next_todo(cards: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    for card in rank_cards(list(cards or [])):
        if str(card.get("status") or CARD_TODO) != CARD_DONE:
            return card
    return None


def remaining_count(cards: list[dict[str, Any]] | None) -> int:
    return sum(
        1
        for card in cards or []
        if str(card.get("status") or CARD_TODO) != CARD_DONE
    )


def mark_done(
    cards: list[dict[str, Any]],
    card_id: str,
    *,
    commit_sha: str = "",
) -> list[dict[str, Any]]:
    updated: list[dict[str, Any]] = []
    for card in cards:
        if str(card.get("id")) == str(card_id):
            copy = dict(card)
            copy["status"] = CARD_DONE
            if commit_sha:
                copy["commit_sha"] = commit_sha
            updated.append(copy)
        else:
            updated.append(dict(card))
    return updated


def _json_blob(text: str) -> str | None:
    body = (text or "").strip()
    if not body:
        return None
    fenced = _FENCE.search(body)
    if fenced:
        body = fenced.group(1).strip()
    for opener, closer in (("[", "]"), ("{", "}")):
        start = body.find(opener)
        end = body.rfind(closer)
        if start != -1 and end > start:
            return body[start : end + 1]
    return None
