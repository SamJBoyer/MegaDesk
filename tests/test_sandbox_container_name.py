"""Pure helpers for MachineFactory agent sandbox names — no Docker required."""

from __future__ import annotations


def test_container_name_includes_guid_and_is_docker_safe() -> None:
    from MachineFactoryManager.pool import CONTAINER_NAME_PREFIX, container_name

    name = container_name("MegaDesk", "Rewrite all gui!", "ABC 123")
    assert name.startswith(CONTAINER_NAME_PREFIX)
    assert "abc-123" in name
    assert " " not in name
    assert "!" not in name
    assert name == name.lower()


def test_container_name_differs_for_two_guids_on_the_same_ticket() -> None:
    from MachineFactoryManager.pool import container_name

    first = container_name("MegaDesk", "same-ticket", "guid-aaa")
    second = container_name("MegaDesk", "same-ticket", "guid-bbb")
    assert first != second
    assert first.endswith("guid-aaa")
    assert second.endswith("guid-bbb")


def test_container_name_truncates_ticket_not_guid() -> None:
    from MachineFactoryManager.pool import (
        CONTAINER_NAME_PREFIX,
        _CONTAINER_NAME_MAX,
        _GUID_TOKEN_LEN,
        container_name,
    )

    guid = "a" * 80
    name = container_name("MegaDesk", "x" * 200, guid)
    guid_token = "a" * _GUID_TOKEN_LEN
    assert name.startswith(CONTAINER_NAME_PREFIX)
    assert name.endswith(guid_token)
    assert len(name) <= _CONTAINER_NAME_MAX
