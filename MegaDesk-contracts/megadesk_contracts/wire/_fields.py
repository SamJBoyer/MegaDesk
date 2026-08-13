"""Field coercion shared by every MegaDesk wire module.

Redis stream fields are strings on the wire, so every builder in this package
emits ``str`` and every parser accepts whatever Redis handed back. Builders
validate rather than coerce silently: a missing required field or an unknown
enum value is a contract break, and the writer is the last place it can still be
diagnosed cheaply.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

BOOL_TRUE = "true"
BOOL_FALSE = "false"

_TRUTHY = frozenset({"1", "true", "yes", "y", "on"})
_FALSEY = frozenset({"0", "false", "no", "n", "off", ""})


def bool_field(value: Any) -> str:
    """Coerce a bool-ish value to the ``"true"`` / ``"false"`` wire form."""
    if isinstance(value, bool):
        return BOOL_TRUE if value else BOOL_FALSE
    text = str(value).strip().lower()
    if text in _TRUTHY:
        return BOOL_TRUE
    if text in _FALSEY:
        return BOOL_FALSE
    raise ValueError(f"Invalid boolean field: {value!r}")


def is_true(value: Any) -> bool:
    return bool_field(value) == BOOL_TRUE


def text_field(value: Any) -> str:
    """Keep interior whitespace: prompts and answers are meaningful verbatim."""
    return "" if value is None else str(value)


def stripped(value: Any) -> str:
    return text_field(value).strip()


def require(kind: str, fields: Mapping[str, str], names: Iterable[str]) -> None:
    """Whitespace counts as missing: a question of three spaces is no question."""
    missing = [name for name in names if not str(fields.get(name) or "").strip()]
    if missing:
        raise ValueError(f"{kind} requires {', '.join(missing)}")


def one_of(kind: str, field: str, value: str, allowed: frozenset[str]) -> str:
    if value not in allowed:
        raise ValueError(
            f"{kind} {field}={value!r} is not one of {', '.join(sorted(allowed))}"
        )
    return value
