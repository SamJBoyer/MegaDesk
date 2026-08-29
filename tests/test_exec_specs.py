"""FE/BE spec split: get_fe_spec lists the Redis launch endpoints."""

from __future__ import annotations


def test_be_nodes_declare_their_launch_endpoints() -> None:
    from cloud_factory_node import get_be_spec as cloud_be
    from cloud_factory_node import get_fe_spec as cloud_fe
    from code_scope_node import get_be_spec as scope_be
    from code_scope_node import get_fe_spec as scope_fe
    from machine_factory_node import get_be_spec as mc_be
    from machine_factory_node import get_fe_spec as mc_fe
    from sargent_node import get_be_spec as sargent_be
    from sargent_node import get_fe_spec as sargent_fe
    from voice_deck_node import get_be_spec as voice_be
    from voice_deck_node import get_fe_spec as voice_fe

    assert mc_fe().backends == ("machine_factory",)
    assert mc_be().name == "machine_factory"
    assert scope_fe().backends == ("code_scope",)
    assert scope_be().name == "code_scope"
    assert voice_fe() is None
    assert voice_be().name == "voice_deck"
    assert cloud_fe().backends == ("cloud_factory",)
    assert cloud_be().name == "cloud_factory"
    assert sargent_fe().backends == ("sargent",)
    assert sargent_be().name == "sargent"


def test_fe_only_nodes_do_not_launch_a_backend() -> None:
    from auto_integrate_node import get_be_spec as ai_be
    from auto_integrate_node import get_fe_spec as ai_fe
    from graph_scope_node import get_be_spec as gs_be
    from graph_scope_node import get_fe_spec as gs_fe
    from pr_manager_node import get_be_spec as pm_be
    from pr_manager_node import get_fe_spec as pm_fe
    from work_dispatcher_node import get_be_spec as wd_be
    from work_dispatcher_node import get_fe_spec as wd_fe

    assert wd_fe().backends == ()
    assert wd_fe().parameters == ("GIT_URL", "ISSUE_LABEL")
    assert wd_fe().read_parameters is not None
    assert wd_be() is None
    assert ai_fe().backends == ()
    assert ai_fe().parameters == ("GIT_URL",)
    assert ai_fe().read_parameters is not None
    assert ai_be() is None
    assert pm_fe().backends == ()
    assert pm_fe().parameters == ("GIT_URL",)
    assert pm_fe().read_parameters is not None
    assert pm_be() is None
    assert gs_fe().backends == ()
    assert gs_fe().name == "graph_scope"
    assert gs_be() is None
