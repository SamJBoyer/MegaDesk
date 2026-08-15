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
        "Nodes/CodeScope",
        "Nodes/VoiceDeck",
        "Nodes/CloudDispatcher",
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

# The voice chain's streams. Defined here rather than read off the wire module
# so a field rename has to be made twice, deliberately, in two places.
CODEQ_ASK_CANONICAL_FIELDS = frozenset(
    {"session_id", "question_id", "repo", "question", "mode"}
)
CODEQ_ANSWER_CANONICAL_FIELDS = frozenset(
    {"session_id", "question_id", "repo", "answer", "final", "status"}
)
CODESCOPE_SESSION_CANONICAL_FIELDS = frozenset(
    {"repo", "clone_path", "agent_id", "model", "status"}
)
VOICE_CONTROL_CANONICAL_FIELDS = frozenset({"action", "value"})
VOICE_EVENT_CANONICAL_FIELDS = frozenset({"kind", "text", "session_id"})
CLOUDORDER_CANONICAL_FIELDS = frozenset(
    {"order_id", "repo_url", "ref", "title", "instructions", "model", "auto_pr"}
)
CLOUDFINISHED_CANONICAL_FIELDS = frozenset(
    {"agent_id", "order_id", "status", "pr_url"}
)
CLOUDRUN_CANONICAL_FIELDS = frozenset(
    {"order_id", "repo_url", "title", "status", "pr_url", "run_id"}
)

WORKORDER_STREAM = "WORKORDER"
WORKORDER_GROUP = "mission_control"
FINISHED_GROUP = "merge_manager"
CODEQ_ASK_GROUP = "code_scope"
CLOUDORDER_GROUP = "cloud_dispatcher"

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
def code_scope_module() -> ModuleType:
    from code_scope_frontend import app

    return app


@pytest.fixture(scope="session")
def voice_deck_module() -> ModuleType:
    from voice_deck_frontend import app

    return app


@pytest.fixture(scope="session")
def cloud_dispatcher_module() -> ModuleType:
    from cloud_dispatcher_frontend import app

    return app


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


# Hashes this suite owns on db 1. Everything else there belongs to whatever
# MegaDesk the developer has running — Supervisor's singleton, its heartbeat, and
# the RUNNINGNODES registry — so these are deleted by prefix and db 1 is never
# flushed.
PERSISTENT_TEST_PREFIXES = (
    "CODESCOPE:SESSION:",
    "CLOUDRUN:",
    "CLOUDDRAFT:",
    "NODEHB:test-",
    "NODE:SHUTDOWN:test-",
)


@pytest.fixture
def persistent_client():
    """A client on db 1, where the session and cloud-run hashes live.

    Production pins these to db 1 the way ``SupervisorClient`` does, so a test
    that used the db-15 stream client instead would pass while the real FE and BE
    talked past each other.
    """
    import redis as redis_lib

    client = redis_lib.Redis.from_url(
        TEST_REDIS_URL,
        db=1,
        decode_responses=True,
        socket_connect_timeout=2,
    )
    try:
        client.ping()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Redis not reachable at {TEST_REDIS_URL}: {exc}")

    def _clear() -> None:
        for prefix in PERSISTENT_TEST_PREFIXES:
            for key in client.scan_iter(match=f"{prefix}*", count=100):
                client.delete(key)

    _clear()
    try:
        yield client
    finally:
        _clear()
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
    from cloud_dispatcher_frontend import app as cloud_dispatcher_app
    from code_scope_frontend import app as code_scope_app
    from mission_control_frontend import app as mission_control_app
    from voice_deck_frontend import app as voice_deck_app

    for module in (
        ticket_dispatcher_app,
        merge_manager_app,
        code_scope_app,
        voice_deck_app,
        cloud_dispatcher_app,
        mission_control_app,
    ):
        monkeypatch.setattr(module, "POLL_INTERVAL_SEC", FAST_POLL_SEC)


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


@pytest.fixture
def origin_repo(git_floor, tmp_path: Path) -> Path:
    """A bare repo named ``widgets.git``, so a clone of it is named ``widgets``.

    CodeScope derives the clone directory from the URL's last segment, and
    ``GitFloor``'s remote is always ``origin.git``.
    """
    from megadesk_contracts.testing import git

    dest = tmp_path / "widgets.git"
    git("clone", "--bare", str(git_floor.origin), str(dest), cwd=tmp_path)
    return dest


@pytest.fixture
def scope_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect CodeScope's clone directory out of the node package."""
    root = tmp_path / "Scope"
    monkeypatch.setenv("SCOPE_ROOT", str(root))
    return root


@pytest.fixture
def fake_code_agent(redis_client):
    """Canned answers about code: no ``cursor_sdk``, no agent, no network."""
    from megadesk_contracts.testing import FakeCodeAgent

    agent = FakeCodeAgent(redis=redis_client, group=CODEQ_ASK_GROUP)
    agent.ensure_group()
    return agent


@pytest.fixture
def code_scope_manager(redis_client, persistent_client, fake_code_agent):
    """The real BE loop with a fake runner: sentence buffering and acks are real."""
    from CodeScopeManager.manager import CodeScopeManager

    manager = CodeScopeManager(
        ephemeral=redis_client,
        persistent=persistent_client,
        runner_factory=fake_code_agent.runner_factory,
        group=CODEQ_ASK_GROUP,
        consumer="test-manager",
    )
    try:
        yield manager
    finally:
        manager.close()


@pytest.fixture
def fake_cloud_runtime():
    """``bc-`` ids and a canned PR URL instead of a Cursor-hosted VM."""
    from megadesk_contracts.testing import FakeCloudRuntime

    return FakeCloudRuntime()


@pytest.fixture
def cloud_dispatcher(redis_client, persistent_client, fake_cloud_runtime):
    """The real BE loop with a fake runtime: the registry and acks are real."""
    from CloudDispatcherManager.dispatcher import CloudDispatcher

    dispatcher = CloudDispatcher(
        ephemeral=redis_client,
        persistent=persistent_client,
        runtime=fake_cloud_runtime,
        group=CLOUDORDER_GROUP,
        consumer="test-dispatcher",
        run_poll_interval=0.0,
        retry_delay=0.0,
    )
    dispatcher.ensure_group()
    return dispatcher


@pytest.fixture
def opened_urls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Catch PR links the FE would open, so no browser appears mid-test."""
    from cloud_dispatcher_frontend import app as cloud_dispatcher_app

    opened: list[str] = []
    monkeypatch.setattr(cloud_dispatcher_app, "open_url", opened.append)
    return opened


@pytest.fixture
def fake_realtime():
    """A scripted realtime socket: no microphone, no websocket, no API key."""
    from megadesk_contracts.testing import FakeRealtime

    return FakeRealtime()


@pytest.fixture
def voice_session(redis_client, persistent_client, fake_realtime):
    """The real VoiceDeck BE with its transport swapped out.

    Everything around the socket stays real: the tool router, the CODEQ payloads,
    the draft-versus-order decision, and the injection path.
    """
    from VoiceDeckManager.session import VoiceSession

    session = VoiceSession(
        ephemeral=redis_client,
        persistent=persistent_client,
        transport_factory=lambda **_kwargs: fake_realtime,
        session_id="voice-test",
    )
    try:
        yield session
    finally:
        session.shutdown()


# --- helpers ---------------------------------------------------------------


@pytest.fixture
def workorders(redis_client):
    """Read WORKORDER entries as (entry_id, fields) in stream order."""

    def _read() -> list[tuple[str, dict[str, str]]]:
        return list(redis_client.xrange(WORKORDER_STREAM))

    return _read


@pytest.fixture
def read_stream(redis_client):
    """Read any stream as (entry_id, fields) in stream order."""

    def _read(stream: str) -> list[tuple[str, dict[str, str]]]:
        return list(redis_client.xrange(stream))

    return _read
