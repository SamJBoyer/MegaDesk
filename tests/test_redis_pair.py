"""REDIS_URL names a pair, not a single database."""

import pytest
from megadesk_contracts import (
    HOST_PYTEST_EPHEMERAL_DB,
    HOST_PYTEST_PERSISTENT_DB,
    REDIS_DB_EPHEMERAL,
    REDIS_DB_PERSISTENT,
    dev_flush_mode_enabled,
    flush_live_redis_pair,
    redis_url_db,
    redis_url_with_db,
    resolve_ephemeral_db,
    resolve_factory_redis_url,
    resolve_persistent_db,
    resolve_redis_pair,
)


def test_live_urls_stay_on_zero_and_one() -> None:
    assert resolve_redis_pair("redis://localhost:6379/0") == (0, 1)
    assert resolve_redis_pair("redis://localhost:6379/1") == (0, 1)
    assert resolve_redis_pair("redis://localhost:6379") == (0, 1)


def test_even_index_is_ephemeral_of_the_pair() -> None:
    assert resolve_redis_pair("redis://localhost:6379/4") == (4, 5)
    assert resolve_redis_pair("redis://localhost:6379/14") == (
        HOST_PYTEST_EPHEMERAL_DB,
        HOST_PYTEST_PERSISTENT_DB,
    )


def test_odd_index_snaps_to_the_pair() -> None:
    assert resolve_redis_pair("redis://localhost:6379/5") == (4, 5)
    assert resolve_redis_pair("redis://localhost:6379/15") == (14, 15)


def test_url_helpers_round_trip() -> None:
    url = redis_url_with_db("redis://localhost:6379/0", 4)
    assert redis_url_db(url) == 4
    assert resolve_ephemeral_db(url) == 4
    assert resolve_persistent_db(url) == 5


def test_factory_url_prefers_the_dedicated_env(monkeypatch) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/4")
    monkeypatch.setenv(
        "MEGADESK_FACTORY_REDIS_URL", "redis://host.docker.internal:6379/0"
    )
    assert resolve_factory_redis_url() == "redis://host.docker.internal:6379/0"
    monkeypatch.delenv("MEGADESK_FACTORY_REDIS_URL")
    assert resolve_factory_redis_url().endswith("/4")


def test_live_constants_are_the_default_pair() -> None:
    assert REDIS_DB_EPHEMERAL == 0
    assert REDIS_DB_PERSISTENT == 1


def test_supervisor_on_wire_key_names() -> None:
    from megadesk_contracts.supervisor_client import (
        KILLREQUEST_STREAM,
        LAUNCHREQUEST_STREAM,
        SUPERVISOR_ALIVE_KEY,
        SUPERVISOR_SINGLETON_KEY,
    )

    assert SUPERVISOR_ALIVE_KEY == "SUPERVISOR:ALIVE"
    assert SUPERVISOR_SINGLETON_KEY == "SUPERVISOR:SINGLETON"
    assert LAUNCHREQUEST_STREAM == "SUPERVISOR:LAUNCHREQUEST"
    assert KILLREQUEST_STREAM == "SUPERVISOR:KILLREQUEST"


def test_redis_connect_rewrites_the_url_path() -> None:
    """redis-py ignores db= when the URL already names a database."""
    from megadesk_contracts import redis_connect

    client = redis_connect("redis://localhost:6379/14", db=15)
    assert client.connection_pool.connection_kwargs.get("db") == 15


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "Yes", "on", "On"])
def test_dev_flush_mode_enabled_truthy(monkeypatch, value: str) -> None:
    monkeypatch.setenv("DEV_FLUSH_MODE", value)
    assert dev_flush_mode_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "off", "Off"])
def test_dev_flush_mode_enabled_falsey(monkeypatch, value: str) -> None:
    monkeypatch.setenv("DEV_FLUSH_MODE", value)
    assert dev_flush_mode_enabled() is False


@pytest.mark.parametrize("value", ["2", "maybe", "enabled"])
def test_dev_flush_mode_enabled_unknown_is_off(monkeypatch, value: str) -> None:
    monkeypatch.setenv("DEV_FLUSH_MODE", value)
    assert dev_flush_mode_enabled() is False


def test_dev_flush_mode_enabled_on_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("DEV_FLUSH_MODE", raising=False)
    assert dev_flush_mode_enabled() is True


def test_dev_flush_mode_enabled_on_when_empty(monkeypatch) -> None:
    monkeypatch.setenv("DEV_FLUSH_MODE", "")
    assert dev_flush_mode_enabled() is True


def test_flush_live_redis_pair_flushes_db_zero_then_one(monkeypatch) -> None:
    """Mocked clients only — never FLUSHDB live 0/1 from pytest."""
    flushed: list[int] = []
    closed: list[int] = []

    class _FakeClient:
        def __init__(self, db: int) -> None:
            self.db = db

        def flushdb(self) -> None:
            flushed.append(self.db)

        def close(self) -> None:
            closed.append(self.db)

    def _fake_connect(_url=None, *, db: int, **_kwargs):
        return _FakeClient(db)

    def _forbid_real_redis(*_args, **_kwargs):
        raise AssertionError("test must not open a real Redis client")

    monkeypatch.setattr(
        "megadesk_contracts.supervisor_client.redis_connect", _fake_connect
    )
    monkeypatch.setattr(
        "megadesk_contracts.supervisor_client.redis.Redis.from_url",
        _forbid_real_redis,
    )
    flush_live_redis_pair("redis://localhost:6379/14")
    assert flushed == [0, 1]
    assert closed == [0, 1]
