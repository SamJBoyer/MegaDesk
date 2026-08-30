"""Canvas Redis auto-provision publishes on loopback only."""

from __future__ import annotations

from unittest.mock import patch

import pytest


def test_redis_publish_args_bind_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    from supervisor.redis_provision import _redis_publish_args

    monkeypatch.delenv("REDIS_PASSWORD", raising=False)
    args = _redis_publish_args(6379)
    assert "-p" in args
    publish = args[args.index("-p") + 1]
    assert publish.startswith("127.0.0.1:")
    assert publish == "127.0.0.1:6379:6379"
    assert "0.0.0.0" not in publish


def test_redis_publish_args_requirepass_only_when_provisioning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from supervisor.redis_provision import _redis_publish_args

    monkeypatch.setenv("REDIS_PASSWORD", "operator-secret")
    args = _redis_publish_args(6379)
    assert args[:2] == ["-p", "127.0.0.1:6379:6379"]
    assert "--requirepass" in args
    assert args[args.index("--requirepass") + 1] == "operator-secret"


def test_insight_is_opt_in_and_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    from supervisor import redis_provision as provision

    monkeypatch.delenv("MEGADESK_REDIS_INSIGHT", raising=False)
    assert provision.redis_insight_enabled() is False
    monkeypatch.setenv("MEGADESK_REDIS_INSIGHT", "1")
    assert provision.redis_insight_enabled() is True

    started: list[tuple[str, list[str]]] = []

    def fake_ensure(name: str, run_args: list[str]) -> None:
        started.append((name, run_args))

    monkeypatch.setattr(provision, "ping_redis", lambda url=None: False)
    monkeypatch.setattr(provision, "_docker_available", lambda: True)
    monkeypatch.setattr(provision, "_ensure_container", fake_ensure)
    with patch.object(provision.time, "time", side_effect=[0, 40]):
        with pytest.raises(RuntimeError, match="never became reachable"):
            provision.provision_redis("redis://localhost:6379/0")

    names = [name for name, _args in started]
    assert provision.REDIS_CONTAINER in names
    assert provision.INSIGHTS_CONTAINER in names
    insight_args = next(
        args for name, args in started if name == provision.INSIGHTS_CONTAINER
    )
    assert "-p" in insight_args
    publish = insight_args[insight_args.index("-p") + 1]
    assert publish == "127.0.0.1:5540:5540"


def test_insight_is_skipped_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from supervisor import redis_provision as provision

    monkeypatch.delenv("MEGADESK_REDIS_INSIGHT", raising=False)
    started: list[str] = []

    def fake_ensure(name: str, run_args: list[str]) -> None:
        started.append(name)

    monkeypatch.setattr(provision, "ping_redis", lambda url=None: False)
    monkeypatch.setattr(provision, "_docker_available", lambda: True)
    monkeypatch.setattr(provision, "_ensure_container", fake_ensure)
    with patch.object(provision.time, "time", side_effect=[0, 40]):
        with pytest.raises(RuntimeError, match="never became reachable"):
            provision.provision_redis("redis://localhost:6379/0")

    assert provision.REDIS_CONTAINER in started
    assert provision.INSIGHTS_CONTAINER not in started
