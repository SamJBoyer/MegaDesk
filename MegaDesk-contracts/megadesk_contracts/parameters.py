"""Node parameters — the values a graph saves on behalf of a node.

A node that takes parameters ships ``parameters.yaml`` next to its entry-point
module, declaring the names it recognizes::

    - GIT_URL   # the http of the git repo this node will connect to

A graph stores a value per declared name per member and hands them back to
``get_fe_spec(parameters=...)`` when the graph loads. What a node does with them
is the node's own business; this module only reads the declaration and coerces
values into the string kvps that both Dear PyGui and Redis can carry.

The file is deliberately parsed without a YAML dependency: it is a flat list of
names, ``#`` starts a comment, and values never appear in it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

PARAMETERS_FILENAME = "parameters.yaml"

# JSON blob of the launch parameters, injected by Supervisor into a BE process.
ENV_PARAMETERS = "MEGADESK_PARAMETERS"


def parameters_path(anchor: str | os.PathLike[str]) -> Path:
    """``parameters.yaml`` beside ``anchor`` — a module file or its directory."""
    path = Path(anchor)
    directory = path if path.is_dir() else path.parent
    return directory / PARAMETERS_FILENAME


def load_parameter_names(anchor: str | os.PathLike[str]) -> tuple[str, ...]:
    """Declared parameter names in file order; empty when there is no file."""
    path = parameters_path(anchor)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ()

    names: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or line == "---":
            continue
        if line.startswith("-"):
            line = line[1:].strip()
        line = line.rstrip(":").strip().strip("'\"")
        if line and line not in names:
            names.append(line)
    return tuple(names)


def coerce_parameters(raw: Any) -> dict[str, str]:
    """Best-effort read of a parameter payload into string kvps.

    Accepts a mapping, a JSON object as text (how the payload crosses Redis),
    or nothing at all. Anything else yields an empty dict rather than raising,
    because this sits on the load path of a user-editable graph file.
    """
    if raw is None:
        return {}
    if isinstance(raw, Mapping):
        return {
            str(key): "" if value is None else str(value)
            for key, value in raw.items()
        }
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            decoded = json.loads(text)
        except (TypeError, ValueError):
            return {}
        return coerce_parameters(decoded) if isinstance(decoded, Mapping) else {}
    return {}


def normalize_parameters(
    raw: Any,
    declared: Optional[Iterable[str]] = None,
) -> dict[str, str]:
    """String kvps, restricted to ``declared`` names when a declaration exists.

    Restricting is what keeps a hand-edited graph from feeding a node keys it
    never announced.
    """
    values = coerce_parameters(raw)
    if declared is None:
        return values
    allowed = tuple(declared)
    if not allowed:
        return {}
    return {name: values[name] for name in allowed if name in values}


def parameters_to_json(values: Any) -> str:
    """Serialize parameters for a Redis field; empty input stays an empty string."""
    coerced = coerce_parameters(values)
    if not coerced:
        return ""
    return json.dumps(coerced, sort_keys=True)


def parameters_from_env(env: Optional[Mapping[str, str]] = None) -> dict[str, str]:
    """Parameters a Supervisor-launched BE was started with."""
    source = os.environ if env is None else env
    return coerce_parameters(source.get(ENV_PARAMETERS, ""))
