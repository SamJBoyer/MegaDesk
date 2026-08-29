"""Pure helpers for MachineFactory Redis sidecars — no Docker required."""

from __future__ import annotations

import pytest


def test_redis_sidecar_name_is_prefixed_and_docker_safe() -> None:
    from MachineFactoryManager.pool import REDIS_NAME_PREFIX, redis_sidecar_name

    assert redis_sidecar_name("abc-123") == f"{REDIS_NAME_PREFIX}abc-123"
    name = redis_sidecar_name("Agent Guid With Spaces!!")
    assert name.startswith(REDIS_NAME_PREFIX)
    assert " " not in name
    assert "!" not in name
    assert name == name.lower()


def test_redis_sidecar_name_truncates_long_guids() -> None:
    from MachineFactoryManager.pool import REDIS_NAME_PREFIX, redis_sidecar_name

    long_guid = "a" * 80
    name = redis_sidecar_name(long_guid)
    assert name.startswith(REDIS_NAME_PREFIX)
    # prefix + up to 48 chars of the sanitized token
    assert len(name) <= len(REDIS_NAME_PREFIX) + 48


def test_factory_redis_url_for_container_uses_host_ephemeral_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from MachineFactoryManager.pool import factory_redis_url_for_container

    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/14")
    monkeypatch.setenv("REDIS_URL_CONTAINER", "redis://host.docker.internal:6379/0")
    assert factory_redis_url_for_container() == "redis://host.docker.internal:6379/14"


def test_factory_redis_url_for_container_defaults_live_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from MachineFactoryManager.pool import factory_redis_url_for_container

    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.delenv("REDIS_URL_CONTAINER", raising=False)
    assert factory_redis_url_for_container() == "redis://host.docker.internal:6379/0"


def test_list_redis_sidecars_reads_run_keys_from_labels() -> None:
    from unittest.mock import patch

    from MachineFactoryManager import pool

    with patch.object(pool, "_docker") as docker:
        docker.return_value.returncode = 0
        docker.return_value.stdout = "guid-1\nguid-2\n"
        assert pool.list_redis_sidecars() == ["guid-1", "guid-2"]
        args = docker.call_args[0][0]
        assert args[0] == "ps"
        assert f"label={pool.REDIS_RUN_LABEL}" in args
        assert f'.Label "{pool.REDIS_RUN_LABEL}"' in args[-1]


def test_list_redis_sidecars_empty_when_docker_fails() -> None:
    from unittest.mock import patch

    from MachineFactoryManager import pool

    with patch.object(pool, "_docker") as docker:
        docker.return_value.returncode = 1
        docker.return_value.stdout = ""
        assert pool.list_redis_sidecars() == []
