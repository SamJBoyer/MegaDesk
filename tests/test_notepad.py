"""Notepad: text files, git include, and the voice-tool seam.

The pad model imports no Dear PyGui. Canvas tests drop the real FE and drive
the same create / append / switch verbs the Redis commands call. VoiceDeck
tests assert the tool router publishes the canonical NOTEPAD:CMD fields.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import NOTEPAD_CMD_CANONICAL_FIELDS
from megadesk_contracts.testing import git
from megadesk_contracts.wire import notepad as wire
from notepad_frontend.pad import Pad, PadError, note_filename, safe_title
from notepad_tools import TOOL_ADD_NOTE_TEXT, TOOL_CREATE_NOTE, TOOL_SWITCH_NOTE

NODE = "notepad"


# --- pad model -------------------------------------------------------------


def test_create_switch_and_append_keep_one_current_document() -> None:
    pad = Pad()
    pad.create("alpha", "hello")
    pad.create("beta")
    assert pad.titles() == ["alpha", "beta"]
    assert pad.current == "beta"

    pad.switch("alpha")
    pad.append(" world")
    assert pad.note("alpha").text == "hello\n world"
    assert pad.current == "alpha"


def test_append_without_a_target_is_an_error() -> None:
    pad = Pad()
    with pytest.raises(PadError, match="no document"):
        pad.append("hello")


def test_titles_become_safe_text_filenames(tmp_path: Path) -> None:
    assert safe_title("Meeting notes") == "Meeting-notes"
    assert note_filename("Meeting notes") == "Meeting-notes.txt"

    pad = Pad(notes_root=tmp_path)
    pad.create("Meeting notes", "ship it")
    written = pad.save()
    path = tmp_path / "Meeting-notes.txt"
    assert written == [path]
    assert path.read_text(encoding="utf-8") == "ship it"

    reloaded = Pad(notes_root=tmp_path)
    reloaded.load()
    assert reloaded.titles() == ["Meeting-notes"]
    assert reloaded.note().text == "ship it"


def test_attach_repo_rejects_non_github_network_urls(tmp_path: Path) -> None:
    pad = Pad(notes_root=tmp_path / "notes")
    with pytest.raises(PadError, match="Unrecognized"):
        pad.attach_repo("https://example.com/acme/widgets", scope=tmp_path / "scope")
    with pytest.raises(PadError, match="Unrecognized"):
        pad.attach_repo("file:///tmp/widgets", scope=tmp_path / "scope")


def test_git_include_stages_the_note_files(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git("init", cwd=repo)
    git("checkout", "-b", "dev", cwd=repo, check=False)

    pad = Pad(notes_root=repo / "notes", repo_root=repo)
    pad.create("plan", "do the thing")
    rels = pad.git_include()

    assert rels == ["notes/plan.txt"]
    staged = git("diff", "--cached", "--name-only", cwd=repo).stdout.split()
    assert staged == ["notes/plan.txt"]


def test_command_writer_emits_only_canonical_fields() -> None:
    fields = wire.command_fields(
        action=wire.ACTION_CREATE, title="plan", text="do the thing"
    )
    assert set(fields) == set(NOTEPAD_CMD_CANONICAL_FIELDS)
    assert all(isinstance(v, str) for v in fields.values())
    parsed = wire.parse_command(fields)
    assert parsed["action"] == "create"
    assert parsed["title"] == "plan"


def test_append_requires_text_and_create_requires_a_title() -> None:
    with pytest.raises(ValueError):
        wire.command_fields(action=wire.ACTION_APPEND, title="plan")
    with pytest.raises(ValueError):
        wire.command_fields(action=wire.ACTION_CREATE, text="hi")


# --- the node on the canvas ------------------------------------------------


def _instance(driver):
    from notepad_frontend import app as notepad_app

    live = driver.live_instance(notepad_app._LIVE)
    assert live is not None, "Notepad FE did not register itself"
    return live


@pytest.mark.canvas
def test_typing_a_note_writes_a_text_file(harness, tmp_path: Path) -> None:
    driver = harness.drop(NODE)
    pad = _instance(driver)

    assert driver.exists("tab_note")
    driver.type_into("body", "ship the node")
    harness.pump(2)

    saved = pad.pad.notes_root / "note.txt"
    assert saved.read_text(encoding="utf-8") == "ship the node"
    assert pad.pad.note("note").text == "ship the node"


@pytest.mark.canvas
def test_plus_opens_a_tab_and_clicking_it_switches(harness) -> None:
    driver = harness.drop(NODE)
    pad = _instance(driver)

    driver.type_into("body", "first")
    driver.click("new_btn")
    harness.pump(2)

    assert driver.exists("tab_note-2")
    assert pad.pad.current == "note-2"
    driver.type_into("body", "second")

    driver.click("tab_note")
    harness.pump(2)
    assert pad.pad.current == "note"
    assert driver.get("body") == "first"


@pytest.mark.canvas
@pytest.mark.redis
def test_a_voice_command_creates_a_tab_and_fills_it(
    harness, redis_client
) -> None:
    driver = harness.drop(NODE)
    pad = _instance(driver)

    redis_client.xadd(
        wire.CMD_STREAM,
        wire.command_fields(
            action=wire.ACTION_CREATE, title="brief", text="from voice"
        ),
    )
    harness.pump(4)

    assert driver.exists("tab_brief")
    assert pad.pad.current == "brief"
    assert driver.get("body") == "from voice"
    assert (pad.pad.notes_root / "brief.txt").read_text(encoding="utf-8") == "from voice"


@pytest.mark.canvas
def test_capture_writes_the_git_url_into_the_graph(
    harness, tmp_path: Path
) -> None:
    from engine.graph_bar import CAPTURE_TAG
    from megadesk_contracts.testing import invoke_callback
    from megadesk_contracts import host as dpg
    import json

    driver = harness.drop(NODE)
    # Set without firing: a fake https URL must not start a clone.
    driver.set("git_url", "https://github.com/acme/widgets")
    invoke_callback(dpg.get_item_callback(CAPTURE_TAG), CAPTURE_TAG, None, None)

    saved = json.loads((tmp_path / "graph.json").read_text(encoding="utf-8"))
    assert (
        saved["members"][driver.member_id]["parameters"]["GIT_URL"]
        == "https://github.com/acme/widgets"
    )


def _local_origin(tmp_path: Path) -> Path:
    repo = tmp_path / "origin"
    repo.mkdir()
    git("init", cwd=repo)
    (repo / "README.md").write_text("origin\n", encoding="utf-8")
    git("add", "README.md", cwd=repo)
    git("commit", "-m", "init", cwd=repo)
    return repo


@pytest.mark.canvas
def test_a_repo_url_loads_notes_and_git_includes_them(
    harness, tmp_path: Path
) -> None:
    origin = _local_origin(tmp_path)
    (origin / "notes").mkdir()
    (origin / "notes" / "brief.txt").write_text("already there", encoding="utf-8")
    git("add", "notes/brief.txt", cwd=origin)
    git("commit", "-m", "note", cwd=origin)

    driver = harness.drop(NODE)
    pad = _instance(driver)
    driver.type_into("git_url", str(origin))
    harness.pump(2)

    assert driver.exists("tab_brief")
    assert pad.pad.note("brief").text == "already there"

    pad.add_text("\nmore", "brief")
    driver.click("include_btn")

    clone = pad.pad.repo_root
    assert clone is not None
    staged = git("diff", "--cached", "--name-only", cwd=clone).stdout.split()
    assert "notes/brief.txt" in staged


# --- voice tools -----------------------------------------------------------


@pytest.mark.redis
def test_voice_note_tools_publish_canonical_commands(
    voice_session, fake_realtime, redis_client
) -> None:
    voice_session.start()

    create_id = fake_realtime.call_tool(
        TOOL_CREATE_NOTE, {"title": "brief", "text": "start"}
    )
    voice_session.pump_events()
    assert fake_realtime.result_for(create_id) == {"status": "ok", "title": "brief"}

    append_id = fake_realtime.call_tool(
        TOOL_ADD_NOTE_TEXT, {"title": "brief", "text": "more"}
    )
    switch_id = fake_realtime.call_tool(TOOL_SWITCH_NOTE, {"title": "other"})
    voice_session.pump_events()

    assert fake_realtime.result_for(append_id)["status"] == "ok"
    assert fake_realtime.result_for(switch_id)["status"] == "ok"

    entries = redis_client.xrange(wire.CMD_STREAM)
    actions = [wire.parse_command(fields) for _id, fields in entries]
    assert [item["action"] for item in actions] == ["create", "append", "switch"]
    assert all(set(item) == set(NOTEPAD_CMD_CANONICAL_FIELDS) for item in actions)
    assert actions[0]["title"] == "brief"
    assert actions[1]["text"] == "more"
    assert actions[2]["title"] == "other"
