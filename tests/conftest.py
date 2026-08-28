"""Fixtures for the agent-piloted integration suite.

See [`Docs/integration_testing.md`](../Docs/integration_testing.md) for what these
tests are for and where the chain is cut.

Nodes are installed editable into the MEGADESK conda env, which may point at a
different checkout. Putting this repo's source directories at the front of
``sys.path`` makes the suite test *this* worktree.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parent.parent

sys.path[:0] = [
    str(ROOT / part)
    for part in (
        "MegaDesk-Contracts",
        "MegaDesk-Canvas",
        "Nodes/TicketDispatcher",
        "Nodes/PRManager",
        "Nodes/CodeScope",
        "Nodes/VoiceDeck",
        "Nodes/Factory/MachineFactory",
        "Nodes/Factory/CloudFactory",
        "Nodes/GraphScope",
    )
]

from megadesk_contracts.supervisor_client import (
    DEFAULT_REDIS_URL,
    HOST_PYTEST_EPHEMERAL_DB,
    REDIS_DB_EPHEMERAL,
    REDIS_DB_PERSISTENT,
    redis_url_db,
    redis_url_with_db,
    resolve_redis_pair,
)

# Redis pair dedicated to tests. Live 0/1 is never flushed. If REDIS_URL already
# names a non-live pair, honor it. Host pytest uses 14/15; sandboxes use
# Redis sidecars (not host DB lanes).
def _isolate_pytest_redis_url() -> str:
    raw = (os.environ.get("REDIS_URL") or DEFAULT_REDIS_URL).strip() or DEFAULT_REDIS_URL
    db = redis_url_db(raw)
    if db in (REDIS_DB_EPHEMERAL, REDIS_DB_PERSISTENT):
        return redis_url_with_db(raw, HOST_PYTEST_EPHEMERAL_DB)
    ephemeral, _persistent = resolve_redis_pair(raw)
    return redis_url_with_db(raw, ephemeral)


os.environ["REDIS_URL"] = _isolate_pytest_redis_url()
TEST_REDIS_URL = os.environ["REDIS_URL"]
TEST_EPHEMERAL_DB, TEST_PERSISTENT_DB = resolve_redis_pair(TEST_REDIS_URL)
PROTECTED_REDIS_DBS = frozenset({REDIS_DB_EPHEMERAL, REDIS_DB_PERSISTENT})

ARTIFACTS_ROOT = ROOT / "tests" / "_artifacts"


@pytest.fixture(autouse=True)
def _isolate_megadesk_logs(tmp_path, monkeypatch):
    """Keep Supervisor session transcripts out of the worktree ``Logs/``."""
    monkeypatch.setenv("MEGADESK_LOGS_ROOT", str(tmp_path / "Logs"))
    monkeypatch.delenv("MEGADESK_LOGS_DIR", raising=False)

# Canonical wire format. Every writer emits these names; parsers require them.
WORKORDER_CANONICAL_FIELDS = frozenset(
    {"repo", "URL", "ticket_name", "instructions", "model", "auto_pr"}
)
FINISHED_CANONICAL_FIELDS = frozenset(
    {"ticket_name", "ticket_id", "status", "pr_url"}
)

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
GRAPHRUN_CANONICAL_FIELDS = frozenset(
    {
        "guid",
        "graph",
        "spec",
        "nodes",
        "current",
        "status",
        "ticket_id",
        "ticket_name",
        "repo",
        "started",
        "updated",
        "error",
    }
)
GRAPHEVENT_CANONICAL_FIELDS = frozenset(
    {"guid", "graph", "node", "status", "detail", "ts"}
)

WORKORDER_STREAM = "WORKORDER"
WORKORDER_GROUP = "machine_factory"
CODEQ_ASK_GROUP = "code_scope"
CLOUDORDER_GROUP = "cloud_factory"

# Background poll intervals, shortened so a test does not wait on production
# cadence. Patched per test; module defaults are untouched.
FAST_POLL_SEC = 0.1


# --- wire contract modules -------------------------------------------------


@pytest.fixture(scope="session")
def machine_wire() -> ModuleType:
    """The one definition of WORKORDER / AGENTHANDLER / FINISHED.

    TicketDispatcher and MachineFactory write to this family and import it
    from here, so there is no second copy to drift.
    """
    from megadesk_contracts.wire import machine

    return machine


@pytest.fixture(scope="session")
def graph_wire() -> ModuleType:
    """The one definition of GRAPHRUN / GRAPHEVENT."""
    from megadesk_contracts.wire import graph

    return graph


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
def cloud_factory_module() -> ModuleType:
    from cloud_factory_frontend import app

    return app


@pytest.fixture(scope="session")
def pr_manager_module() -> ModuleType:
    import pr_manager_app

    return pr_manager_app


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
    assert db == TEST_EPHEMERAL_DB and db not in PROTECTED_REDIS_DBS, (
        f"Refusing to flush Redis DB {db}; tests only own pair "
        f"{TEST_EPHEMERAL_DB}/{TEST_PERSISTENT_DB}"
    )
    client.flushdb()
    try:
        yield client
    finally:
        client.flushdb()
        client.close()


@pytest.fixture
def persistent_client():
    """A flushed client on the test persistent DB (15 when host pytest owns 14/15).

    Production pins hashes to the process pair's persistent half the way
    ``SupervisorClient`` does, so a test that used the ephemeral client instead
    would pass while the real FE and BE talked past each other. Live db 1 is
    never flushed.
    """
    import redis as redis_lib

    client = redis_lib.Redis.from_url(
        redis_url_with_db(TEST_REDIS_URL, TEST_PERSISTENT_DB),
        decode_responses=True,
        socket_connect_timeout=2,
    )
    try:
        client.ping()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Redis not reachable at {TEST_REDIS_URL}: {exc}")

    db = client.connection_pool.connection_kwargs.get("db")
    assert db == TEST_PERSISTENT_DB and db not in PROTECTED_REDIS_DBS, (
        f"Refusing to flush Redis DB {db}; tests only own pair "
        f"{TEST_EPHEMERAL_DB}/{TEST_PERSISTENT_DB}"
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
    import pr_manager_app
    import ticket_dispatcher_app
    from cloud_factory_frontend import app as cloud_factory_app
    from code_scope_frontend import app as code_scope_app
    from graph_scope_frontend import app as graph_scope_app
    from machine_factory_frontend import app as machine_factory_app
    from voice_deck_frontend import app as voice_deck_app

    for module in (
        ticket_dispatcher_app,
        pr_manager_app,
        code_scope_app,
        voice_deck_app,
        cloud_factory_app,
        machine_factory_app,
        graph_scope_app,
    ):
        monkeypatch.setattr(module, "POLL_INTERVAL_SEC", FAST_POLL_SEC)


@pytest.fixture
def harness(tmp_path: Path, artifacts_dir: Path, fast_polling: None):
    """A booted canvas on an isolated graph, torn down completely afterwards.

    Function-scoped: each test gets a fresh DPG context, which is only safe
    because ``frame_pump.reset()`` runs on teardown.
    """
    from megadesk_contracts import frame_pump
    from megadesk_contracts.testing import CanvasHarness

    frame_pump.reset()
    canvas = CanvasHarness(
        graph_path=tmp_path / "graph.json",
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
def fake_gh(
    monkeypatch: pytest.MonkeyPatch,
    ticket_dispatcher_module: ModuleType,
    pr_manager_module: ModuleType,
):
    """Swap both GitHub pollers' ``run_gh`` for canned answers — no network, no auth."""
    from megadesk_contracts.testing import FakeGh

    gh = FakeGh()
    monkeypatch.setattr(ticket_dispatcher_module, "run_gh", gh)
    monkeypatch.setattr(pr_manager_module, "run_gh", gh)
    return gh


@pytest.fixture
def fake_agent(redis_client, machine_wire: ModuleType):
    """The sandbox stand-in: real consumer group, canned PR, real FINISHED payload."""
    from megadesk_contracts.testing import FakeAgent

    agent = FakeAgent(
        redis=redis_client,
        wire=machine_wire,
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
def fake_cloud_factory():
    """``bc-`` ids and a canned PR URL instead of a Cursor-hosted VM."""
    from megadesk_contracts.testing import FakeCloudFactory

    return FakeCloudFactory()


@pytest.fixture
def cloud_factory(redis_client, persistent_client, fake_cloud_factory):
    """The real BE loop with a fake runtime: the registry and acks are real."""
    from CloudFactoryManager.manager import CloudFactoryManager

    manager = CloudFactoryManager(
        ephemeral=redis_client,
        persistent=persistent_client,
        runtime=fake_cloud_factory,
        group=CLOUDORDER_GROUP,
        consumer="test-cloud-factory",
        run_poll_interval=0.0,
        retry_delay=0.0,
    )
    manager.ensure_group()
    return manager


@pytest.fixture
def fake_machine_factory():
    """Sandbox guids and a container that stops when told, instead of Docker."""
    from megadesk_contracts.testing import FakeMachineFactory

    return FakeMachineFactory()


@pytest.fixture
def machine_factory(redis_client, fake_machine_factory):
    """The real BE loop with a fake sandbox host: wire and acks are real.

    ``orphan_grace=0`` because the grace period exists to survive the moment
    between a sandbox exiting and its hash being deleted, and a test that waited
    it out would spend 30 seconds proving nothing.
    """
    from MachineFactoryManager.manager import MachineFactoryManager

    manager = MachineFactoryManager(
        redis=redis_client,
        runtime=fake_machine_factory,
        group=WORKORDER_GROUP,
        consumer="test-machine-factory",
        run_poll_interval=0.0,
        orphan_grace=0.0,
    )
    return manager


@pytest.fixture
def fake_realtime():
    """A scripted realtime socket: no microphone, no websocket, no API key."""
    from megadesk_contracts.testing import FakeRealtime

    return FakeRealtime()


@pytest.fixture
def voice_session(redis_client, persistent_client, fake_realtime):
    """The real VoiceDeck BE with its transport swapped out.

    Everything around the socket stays real: the tool router, the CODEQ payloads,
    and the injection path.
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
