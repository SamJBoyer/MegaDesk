"""Canvas Redis auto-provision publishes MegaDesk Redis on loopback only."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from megadesk_contracts.supervisor_client import DEFAULT_REDIS_PORT, DEFAULT_REDIS_URL


def test_default_redis_url_is_the_megadesk_port() -> None:
    from megadesk_contracts.supervisor_client import redis_url_host_port

    host, port = redis_url_host_port(DEFAULT_REDIS_URL)
    assert host in {"localhost", "127.0.0.1"}
    assert port == DEFAULT_REDIS_PORT
    assert port != 6379


def test_redis_publish_args_bind_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    from supervisor.redis_provision import REDIS_CONTAINER_PORT, _redis_publish_args

    monkeypatch.delenv("REDIS_PASSWORD", raising=False)
    args = _redis_publish_args(DEFAULT_REDIS_PORT)
    assert "-p" in args
    publish = args[args.index("-p") + 1]
    assert publish.startswith("127.0.0.1:")
    assert publish == f"127.0.0.1:{DEFAULT_REDIS_PORT}:{REDIS_CONTAINER_PORT}"
    assert "0.0.0.0" not in publish


def test_redis_publish_args_requirepass_only_when_provisioning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from supervisor.redis_provision import REDIS_CONTAINER_PORT, _redis_publish_args

    monkeypatch.setenv("REDIS_PASSWORD", "operator-secret")
    args = _redis_publish_args(DEFAULT_REDIS_PORT)
    assert args[:2] == [
        "-p",
        f"127.0.0.1:{DEFAULT_REDIS_PORT}:{REDIS_CONTAINER_PORT}",
    ]
    assert "--requirepass" in args
    assert args[args.index("--requirepass") + 1] == "operator-secret"


def test_insight_is_on_by_default_and_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    from supervisor import redis_provision as provision

    monkeypatch.delenv("MEGADESK_REDIS_INSIGHT", raising=False)
    assert provision.redis_insight_enabled() is True
    monkeypatch.setenv("MEGADESK_REDIS_INSIGHT", "0")
    assert provision.redis_insight_enabled() is False
    monkeypatch.setenv("MEGADESK_REDIS_INSIGHT", "1")
    assert provision.redis_insight_enabled() is True

    monkeypatch.delenv("MEGADESK_REDIS_INSIGHT", raising=False)
    started: list[tuple[str, list[str]]] = []

    def fake_ensure(name: str, run_args: list[str], **_kwargs: object) -> None:
        started.append((name, run_args))

    monkeypatch.setattr(provision, "ping_redis", lambda url=None: False)
    monkeypatch.setattr(provision, "_docker_available", lambda: True)
    monkeypatch.setattr(provision, "_ensure_container", fake_ensure)
    with patch.object(provision.time, "time", side_effect=[0, 40]):
        with pytest.raises(RuntimeError, match="never became reachable"):
            provision.provision_redis(DEFAULT_REDIS_URL)

    names = [name for name, _args in started]
    assert provision.REDIS_CONTAINER in names
    assert provision.INSIGHTS_CONTAINER in names
    insight_args = next(
        args for name, args in started if name == provision.INSIGHTS_CONTAINER
    )
    assert "-p" in insight_args
    publish = insight_args[insight_args.index("-p") + 1]
    assert publish == "127.0.0.1:5540:5540"


def test_insight_can_be_opted_out(monkeypatch: pytest.MonkeyPatch) -> None:
    from supervisor import redis_provision as provision

    monkeypatch.setenv("MEGADESK_REDIS_INSIGHT", "0")
    started: list[str] = []

    def fake_ensure(name: str, run_args: list[str], **_kwargs: object) -> None:
        started.append(name)

    monkeypatch.setattr(provision, "ping_redis", lambda url=None: False)
    monkeypatch.setattr(provision, "_docker_available", lambda: True)
    monkeypatch.setattr(provision, "_ensure_container", fake_ensure)
    with patch.object(provision.time, "time", side_effect=[0, 40]):
        with pytest.raises(RuntimeError, match="never became reachable"):
            provision.provision_redis(DEFAULT_REDIS_URL)

    assert provision.REDIS_CONTAINER in started
    assert provision.INSIGHTS_CONTAINER not in started


def test_provision_boots_megadesk_redis_even_when_url_already_pongs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A random Redis on 6379 must not win over MegaDesk Redis."""
    from supervisor import redis_provision as provision

    started: list[str] = []

    def fake_ensure(name: str, run_args: list[str], **_kwargs: object) -> None:
        started.append(name)

    monkeypatch.delenv("MEGADESK_REDIS_INSIGHT", raising=False)
    monkeypatch.setattr(provision, "ping_redis", lambda url=None: True)
    monkeypatch.setattr(provision, "_docker_available", lambda: True)
    monkeypatch.setattr(provision, "_ensure_container", fake_ensure)
    monkeypatch.setattr(provision, "connect_handles", lambda url=None: "handles")

    assert provision.provision_redis(DEFAULT_REDIS_URL) == "handles"
    assert provision.REDIS_CONTAINER in started
    assert provision.INSIGHTS_CONTAINER in started


def test_provision_attaches_without_docker_when_megadesk_url_is_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from supervisor import redis_provision as provision

    started: list[str] = []

    def fake_ensure(name: str, run_args: list[str], **_kwargs: object) -> None:
        started.append(name)

    monkeypatch.setattr(provision, "ping_redis", lambda url=None: True)
    monkeypatch.setattr(provision, "_docker_available", lambda: False)
    monkeypatch.setattr(provision, "_ensure_container", fake_ensure)
    monkeypatch.setattr(provision, "connect_handles", lambda url=None: "handles")

    assert provision.provision_redis(DEFAULT_REDIS_URL) == "handles"
    assert started == []


def test_provision_falls_back_when_docker_cannot_bind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from supervisor import redis_provision as provision

    def boom(name: str, run_args: list[str], **_kwargs: object) -> None:
        raise RuntimeError("port already allocated")

    monkeypatch.setattr(provision, "ping_redis", lambda url=None: True)
    monkeypatch.setattr(provision, "_docker_available", lambda: True)
    monkeypatch.setattr(provision, "_ensure_container", boom)
    monkeypatch.setattr(provision, "connect_handles", lambda url=None: "handles")

    assert provision.provision_redis(DEFAULT_REDIS_URL) == "handles"
