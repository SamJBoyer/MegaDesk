"""FE/BE spec split: get_fe_spec lists the Redis launch endpoints."""

from __future__ import annotations


def test_be_nodes_declare_their_launch_endpoints() -> None:
    from cloud_dispatcher_node import get_be_spec as cloud_be
    from cloud_dispatcher_node import get_fe_spec as cloud_fe
    from code_scope_node import get_be_spec as scope_be
    from code_scope_node import get_fe_spec as scope_fe
    from mission_control_node import get_be_spec as mc_be
    from mission_control_node import get_fe_spec as mc_fe
    from voice_deck_node import get_be_spec as voice_be
    from voice_deck_node import get_fe_spec as voice_fe

    assert mc_fe().backends == ("mission_control",)
    assert mc_be().name == "mission_control"
    assert scope_fe().backends == ("code_scope",)
    assert scope_be().name == "code_scope"
    assert voice_fe().backends == ("voice_deck",)
    assert voice_be().name == "voice_deck"
    assert cloud_fe().backends == ("cloud_dispatcher",)
    assert cloud_be().name == "cloud_dispatcher"


def test_fe_only_nodes_do_not_launch_a_backend() -> None:
    from merge_manager_node import get_be_spec as mm_be
    from merge_manager_node import get_fe_spec as mm_fe
    from ticket_dispatcher_node import get_be_spec as td_be
    from ticket_dispatcher_node import get_fe_spec as td_fe

    assert td_fe().backends == ()
    assert td_be() is None
    assert mm_fe().backends == ()
    assert mm_be() is None
