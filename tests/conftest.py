"""Fixtures for the agent-piloted integration suite.

See [`Docs/integration_testing.md`](../Docs/integration_testing.md) for what these
tests are for and where the chain is cut.

Two environment facts shape this file:

* Nodes are installed editable into the MEGADESK conda env, which may point at a
  different checkout. Putting this repo's source directories at the front of
  ``sys.path`` makes the suite test *this* worktree.
* MergeManager and MissionControl both ship a top-level ``redis_packets``
  module, so plain ``import redis_packets`` is ambiguous. The path order below
  fixes the winner to MergeManager's copy (the superset, and the one that wins
  in the installed env), and the second copy is loaded explicitly by path.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parent.parent

sys.path[:0] = [
    str(ROOT / part)
    for part in (
        "MegaDesk-contracts",
        "MegaDesk-Canvas",
        "Nodes/TicketDispatcher",
        "Nodes/MergeManager",
        "Nodes/MissionControl",
    )
]

# Redis DB dedicated to tests: assertions need a known stream state and the
# dismissal path calls XDEL, so this must never be the DB carrying dev traffic.
# Set before anything imports a node, since each FE reads REDIS_URL on init.
TEST_REDIS_DB = 15
TEST_REDIS_URL = f"redis://localhost:6379/{TEST_REDIS_DB}"
os.environ["REDIS_URL"] = TEST_REDIS_URL

ARTIFACTS_ROOT = ROOT / "tests" / "_artifacts"

# Canonical wire format. Parsers accept aliases (REPO, ticket, workpath) but
# every writer emits these names only, so tests assert on these exactly —
# otherwise a writer drifting to an alias would still pass.
WORKORDER_CANONICAL_FIELDS = frozenset(
    {"repo", "URL", "new_wt", "wt", "ticket_name", "instructions", "model"}
)
FINISHED_CANONICAL_FIELDS = frozenset({"ticket_name", "ticket_id", "wt", "agent_dir"})

WORKORDER_STREAM = "WORKORDER"
WORKORDER_GROUP = "mission_control"
FINISHED_GROUP = "merge_manager"

# Background poll intervals, shortened so a test does not wait on production
# cadence. Patched per test; module defaults are untouched.
FAST_POLL_SEC = 0.1


def _load_by_path(name: str, path: Path) -> ModuleType:
    """Import a module from an explicit file, bypassing sys.path ambiguity."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - packaging error
        raise ImportError(f"Cannot load {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# --- wire contract modules -------------------------------------------------


@pytest.fixture(scope="session")
def mm_wire() -> ModuleType:
    """MergeManager's redis_packets — the writer of the conflict WORKORDER."""
    import redis_packets

    expected = ROOT / "Nodes" / "MergeManager" / "redis_packets.py"
    assert Path(redis_packets.__file__) == expected, (
        f"'import redis_packets' resolved to {redis_packets.__file__}, expected "
        f"{expected}. Both MergeManager and MissionControl ship a top-level "
        "redis_packets module; check the sys.path order in conftest."
    )
    return redis_packets


@pytest.fixture(scope="session")
def mc_wire() -> ModuleType:
    """MissionControl's redis_packets — the writer of FINISHED."""
    return _load_by_path(
        "mission_control_redis_packets",
        ROOT / "Nodes" / "MissionControl" / "redis_packets.py",
    )


@pytest.fixture(scope="session")
def ticket_dispatcher_module() -> ModuleType:
    import ticket_dispatcher_app

    return ticket_dispatcher_app


@pytest.fixture(scope="session")
def merge_manager_module() -> ModuleType:
    import merge_manager_app

    return merge_manager_app


# --- Redis -----------------------------------------------------------------


@pytest.fixture
def redis_client():
    """A flushed client on the test DB. Skips when no Redis is reachable."""
    import redis as redis_lib

    client = redis_lib.Redis.from_url(
        TEST_REDIS_URL, decode_responses=True, socket_connect_timeout=2
    )
    try:
        client.ping()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Redis not reachable at {TEST_REDIS_URL}: {exc}")

    db = client.connection_pool.connection_kwargs.get("db")
    assert db == TEST_REDIS_DB, (
        f"Refusing to flush Redis DB {db}; tests only own DB {TEST_REDIS_DB}"
    )
    client.flushdb()
    try:
        yield client
    finally:
        client.flushdb()
        client.close()


# --- canvas ----------------------------------------------------------------


@pytest.fixture
def artifacts_dir(request: pytest.FixtureRequest) -> Path:
    """Per-test directory for screenshots, kept in the repo so agents can find them."""
    safe = request.node.name.replace("[", "_").replace("]", "").replace("/", "_")
    path = ARTIFACTS_ROOT / safe
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def fast_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shorten FE background poll intervals so tests do not wait on real cadence."""
    import merge_manager_app
    import ticket_dispatcher_app

    monkeypatch.setattr(ticket_dispatcher_app, "POLL_INTERVAL_SEC", FAST_POLL_SEC)
    monkeypatch.setattr(merge_manager_app, "POLL_INTERVAL_SEC", FAST_POLL_SEC)


@pytest.fixture
def harness(tmp_path: Path, artifacts_dir: Path, fast_polling: None):
    """A booted canvas on an isolated board, torn down completely afterwards.

    Function-scoped: each test gets a fresh DPG context, which is only safe
    because ``frame_pump.reset()`` runs on teardown.
    """
    from megadesk_contracts import frame_pump
    from megadesk_contracts.testing import CanvasHarness

    frame_pump.reset()
    canvas = CanvasHarness(
        canvas_path=tmp_path / "canvas.json",
        artifacts_dir=artifacts_dir,
        supervisor_panel=False,
    )
    canvas.boot()
    try:
        yield canvas
    finally:
        canvas.shutdown()
        frame_pump.reset()


# --- git Floor and fakes ---------------------------------------------------


@pytest.fixture
def git_floor(tmp_path: Path):
    """A real git Floor with a pushable local origin."""
    from megadesk_contracts.testing import GitFloor

    floor = GitFloor(tmp_path / "floor-root", repo="widgets")
    floor.create()
    try:
        yield floor
    finally:
        floor.destroy()


@pytest.fixture
def fake_gh(monkeypatch: pytest.MonkeyPatch, ticket_dispatcher_module: ModuleType):
    """Swap TicketDispatcher's ``run_gh`` for canned answers — no network, no auth."""
    from megadesk_contracts.testing import FakeGh

    gh = FakeGh()
    monkeypatch.setattr(ticket_dispatcher_module, "run_gh", gh)
    return gh


@pytest.fixture
def fake_agent(redis_client, git_floor, mc_wire: ModuleType):
    """The sandbox stand-in: real consumer group, real git, real FINISHED payload."""
    from megadesk_contracts.testing import FakeAgent

    agent = FakeAgent(
        redis=redis_client,
        floor=git_floor,
        wire=mc_wire,
        group=WORKORDER_GROUP,
    )
    agent.ensure_group()
    return agent


# --- helpers ---------------------------------------------------------------


@pytest.fixture
def workorders(redis_client):
    """Read WORKORDER entries as (entry_id, fields) in stream order."""

    def _read() -> list[tuple[str, dict[str, str]]]:
        return list(redis_client.xrange(WORKORDER_STREAM))

    return _read
