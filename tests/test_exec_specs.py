"""FE/BE spec split: get_fe_spec lists the Redis launch endpoints."""

from __future__ import annotations


def test_be_nodes_declare_their_launch_endpoints() -> None:
    from megadesk_contracts import KIND_CLOUD, KIND_MACHINE
    from cloud_factory_node import get_be_spec as cloud_be
    from cloud_factory_node import get_fe_spec as cloud_fe
    from code_scope_node import get_be_spec as scope_be
    from code_scope_node import get_fe_spec as scope_fe
    from machine_factory_node import get_be_spec as mc_be
    from machine_factory_node import get_fe_spec as mc_fe
    from sargent_node import get_be_spec as promptimprover_be
    from sargent_node import get_fe_spec as promptimprover_fe
    from voice_deck_node import get_be_spec as voice_be
    from voice_deck_node import get_fe_spec as voice_fe

    assert mc_fe().backends == ("machine_factory",)
    assert mc_be().name == "machine_factory"
    assert mc_fe().default_width <= 420
    assert mc_fe().default_height <= 120
    assert mc_fe().kind == KIND_MACHINE
    assert scope_fe().kind == KIND_CLOUD
    assert scope_fe().backends == ()
    assert scope_be() is None
    assert voice_fe() is None
    assert voice_be().name == "voice_deck"
    assert cloud_fe().backends == ("cloud_factory",)
    assert cloud_be().name == "cloud_factory"
    assert promptimprover_fe().backends == ("promptimprover",)
    assert promptimprover_be().name == "promptimprover"


def test_fe_only_nodes_do_not_launch_a_backend() -> None:
    from auto_integrate_node import get_be_spec as ai_be
    from auto_integrate_node import get_fe_spec as ai_fe
    from graph_scope_node import get_be_spec as gs_be
    from graph_scope_node import get_fe_spec as gs_fe
    from notepad_node import get_be_spec as np_be
    from notepad_node import get_fe_spec as np_fe
    from pr_manager_node import get_be_spec as pm_be
    from pr_manager_node import get_fe_spec as pm_fe
    from work_dispatcher_node import get_be_spec as wd_be
    from work_dispatcher_node import get_fe_spec as wd_fe
    from work_dispatcher_node import read_sequence

    assert wd_fe().backends == ()
    assert wd_fe().parameters == ("GIT_URL", "ISSUE_LABEL", "MAX_DEPTH")
    assert wd_fe().read_parameters is not None
    assert wd_fe().default_width >= 900
    assert callable(read_sequence)
    assert wd_be() is None
    assert ai_fe().backends == ()
    assert ai_fe().parameters == ("GIT_URL", "MAX_DEPTH")
    assert ai_fe().read_parameters is not None
    assert ai_be() is None
    assert pm_fe().backends == ()
    assert pm_fe().parameters == ("GIT_URL", "MAX_DEPTH")
    assert pm_fe().read_parameters is not None
    assert pm_be() is None
    assert gs_fe().backends == ()
    assert gs_fe().name == "graph_scope"
    assert gs_be() is None
    assert np_fe().backends == ()
    assert np_fe().name == "notepad"
    assert np_fe().parameters == ("GIT_URL",)
    assert np_fe().read_parameters is not None
    assert np_be() is None


def test_nodes_with_voice_tools_declare_them() -> None:
    from code_scope_node import get_tool_spec as scope_tools
    from notepad_node import get_tool_spec as notepad_tools
    from sargent_node import get_tool_spec as promptimprover_tools
    from voice_deck_node import get_tool_spec as voice_tools
    from work_dispatcher_node import get_tool_spec as wd_tools
    from work_dispatcher_node import get_fe_spec as wd_fe

    code = scope_tools()
    tickets = wd_tools()
    notes = notepad_tools()
    rewrite = promptimprover_tools()
    session = voice_tools()
    assert (
        code is not None
        and tickets is not None
        and notes is not None
        and rewrite is not None
        and session is not None
    )
    assert code.name == "code_scope"
    assert {schema["name"] for schema in code.schemas} == {
        "ask_codebase",
        "dispatch_doc_agent",
        "set_repo",
    }
    assert tickets.name == wd_fe().name
    assert {schema["name"] for schema in tickets.schemas} == {
        "list_tickets",
        "choose_ticket",
        "set_dispatch",
        "send_ticket",
    }
    assert notes.name == "notepad"
    assert {schema["name"] for schema in notes.schemas} == {
        "create_note",
        "add_note_text",
        "switch_note",
    }
    assert rewrite.name == "promptimprover"
    assert {schema["name"] for schema in rewrite.schemas} == {"revise_my_prompt"}
    assert set(rewrite.handlers) == {schema["name"] for schema in rewrite.schemas}
    assert session.name == "voice_deck"
    assert {schema["name"] for schema in session.schemas} == {"end_session"}
    assert set(code.handlers) == {schema["name"] for schema in code.schemas}
    assert set(tickets.handlers) == {schema["name"] for schema in tickets.schemas}
    assert set(notes.handlers) == {schema["name"] for schema in notes.schemas}


def test_nodes_without_voice_tools_do_not_declare_them() -> None:
    import auto_integrate_node
    import graph_scope_node
    import machine_factory_node
    import pr_manager_node

    for mod in (
        auto_integrate_node,
        graph_scope_node,
        machine_factory_node,
        pr_manager_node,
    ):
        fn = getattr(mod, "get_tool_spec", None)
        assert fn is None or fn() is None
