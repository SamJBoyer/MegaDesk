"""Thin FE/BE launch specs for MegaDesk nodes, plus voice ToolSpec."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Optional, Protocol, runtime_checkable

KIND_MACHINE = "machine"
KIND_CLOUD = "cloud"
NODE_KINDS = (KIND_MACHINE, KIND_CLOUD)


@dataclass(frozen=True)
class FeSpec:
    """Front-end description for MegaDesk graph hosting.

    ``build`` fills a host-owned content parent (never creates its own window):

        build(parent, *, tag_prefix, width=…, height=…) -> None

    MegaDesk owns the shell (header, close, position, size). The FE only adds
    widgets under ``parent``. Store cleanup on the parent with
    ``dpg.set_item_user_data(parent, cleanup_fn)``.

    ``kind`` is ``machine`` (process on this computer, Supervisor-launched BE)
    or ``cloud`` (process elsewhere; this FE is a client). Catalog groups by it.
    Default ``machine``. Cloud nodes return no ``BeSpec`` and empty ``backends``.

    ``backends`` is the set of Supervisor ``node_endpoint`` names the canvas
    ``XADD``s to ``SUPERVISOR:LAUNCHREQUEST`` when this FE is hosted (drop or graph open).
    Empty means this FE does not start a BE.

    Parameters (see ``megadesk_contracts.parameters``): ``get_fe_spec`` receives
    the values a graph saved for this member and returns a spec that already has
    them folded in — ``build`` closes over them, ``backend_parameters`` carries
    whichever subset the BE needs on ``SUPERVISOR:LAUNCHREQUEST``, and ``parameters``
    declares the names this node recognizes (usually straight from its
    ``parameters.yaml``). ``read_parameters`` reads current values back out of a
    live instance, given the ``tag_prefix`` the host built it with, so the graph
    bar can capture what the operator typed.
    """

    name: str
    description: str
    icon: str | None
    default_width: int
    default_height: int
    build: Callable[..., None]
    backends: tuple[str, ...] = ()
    parameters: tuple[str, ...] = ()
    backend_parameters: Mapping[str, str] = field(default_factory=dict)
    read_parameters: Callable[[str], Mapping[str, str]] | None = None
    kind: str = KIND_MACHINE


@dataclass(frozen=True)
class BeSpec:
    """Back-end launch instruction for Supervisor subprocess management."""

    name: str
    argv: list[str]
    cwd: str | None = None


@runtime_checkable
class ToolHost(Protocol):
    """What VoiceDeck gives a node tool handler when the model calls it.

    Handlers live in the node. The voice session is the host: Redis, the
    loaded CodeScope target (HTTP client of the cloud node), and the
    pending-question map the answer pump already watches.
    """

    target_repo: str
    session_id: str
    last_user_text: str
    current_call_id: str

    @property
    def ephemeral(self) -> Any: ...

    @property
    def persistent(self) -> Any: ...

    @property
    def pending(self) -> dict[str, str]: ...

    def publish(self, kind: str, text: str) -> None: ...

    def set_state(self, state: str) -> None: ...

    def stop(self) -> None: ...

    def resolve_scope_session(self, repo: str = "") -> Optional[tuple[str, str]]: ...

    def loaded_repos(self) -> list[str]: ...

    def repo_url(self, scope_session_id: str) -> str: ...

    def remember_question(self, question_id: str, call_id: str) -> None: ...

    def queue_scope_ask(
        self,
        session_id: str,
        question: str,
        question_id: str,
        *,
        mode: str = "",
    ) -> None: ...


@dataclass(frozen=True)
class ToolSpec:
    """Voice tools a node offers to VoiceDeck, discovered like FeSpec / BeSpec.

    ``get_tool_spec()`` on the MegaDesk.nodes entry module returns this, or
    ``None`` when the node has nothing to say out loud. ``schemas`` are OpenAI
    realtime ``session.tools`` entries. ``instructions`` are folded into the
    voice session prompt. ``handlers`` map each schema's ``name`` to
    ``(arguments, host) -> dict``.
    """

    name: str
    instructions: str
    schemas: tuple[dict, ...]
    handlers: Mapping[str, Callable[..., dict]] = field(default_factory=dict)


def compose_tool_specs(
    specs: Iterable[ToolSpec],
) -> tuple[str, list[dict], dict[str, Callable[..., dict]]]:
    """Merge node tool catalogs into one prompt, schema list, and handler map.

    VoiceDeck's own spec is first so hang-up rules wrap everything else.
    Duplicate tool names keep the first schema and the last handler.
    """
    ordered = sorted(
        specs,
        key=lambda spec: (0 if spec.name == "voice_deck" else 1, spec.name),
    )
    instruction_parts: list[str] = []
    schemas: list[dict] = []
    handlers: dict[str, Callable[..., dict]] = {}
    seen: set[str] = set()
    for spec in ordered:
        text = (spec.instructions or "").strip()
        if text:
            instruction_parts.append(text)
        for schema in spec.schemas:
            name = str(schema.get("name") or "")
            if not name or name in seen:
                continue
            seen.add(name)
            schemas.append(schema)
        handlers.update(spec.handlers)
    return "\n\n".join(instruction_parts), schemas, handlers
