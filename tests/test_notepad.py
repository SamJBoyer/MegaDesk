"""Notepad: pad model, git include, and the canvas / voice seam.

The geometry half needs no desktop session — ``pad.py`` imports no Dear PyGui.
The canvas half boots the real canvas and drives the same widgets a click would.
Voice tools are covered in ``test_voicedeck_flow``; here we assert the FE
applies the same ``NOTEPAD:COMMAND`` entries those tools publish.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import NOTEPAD_COMMAND_CANONICAL_FIELDS
from megadesk_contracts.testing.gitfloor import git
from megadesk_contracts.wire import notepad as wire

from notepad_frontend.pad import (
    Pad,
    PadError,
    apply_command,
    default_pad_root,
    safe_note_name,
)

NODE = "notepad"


# --- pad model -------------------------------------------------------------


def test_safe_note_name_keeps_a_usable_stem() -> None:
    assert safe_note_name("standup notes") == "standup-notes"
    assert safe_note_name("foo/bar") == "foo-bar"
    assert safe_note_name("..") == ""
    assert safe_note_name("") == ""


def test_create_append_and_switch_keep_one_target() -> None:
    pad = Pad()
    first = pad.create("standup")
    assert first.name == "standup"
    assert pad.current == "standup"

    pad.create("ideas")
    pad.append("ship it", "standup")
    assert pad.documents["standup"].text == "ship it"
    assert pad.current == "ideas"

    pad.switch("standup")
    pad.append(" again")
    assert pad.current == "standup"
    assert pad.documents["standup"].text == "ship it\n again"


def test_switch_to_a_missing_document_is_an_error() -> None:
    pad = Pad()
    pad.create("standup")
    with pytest.raises(PadError, match="not open"):
        pad.switch("ideas")


def test_save_writes_txt_files_and_git_includes_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    seed.mkdir()
    git("init", "-b", "dev", ".", cwd=seed)
    (seed / "README.md").write_text("notes repo\n", encoding="utf-8")
    git("add", "README.md", cwd=seed)
    git("commit", "-m", "seed", cwd=seed)
    git("clone", "--bare", str(seed), str(origin), cwd=tmp_path)

    monkeypatch.setenv("NOTEPAD_ROOT", str(tmp_path / "Pad"))
    pad = Pad()
    pad.attach_repo(str(origin), root=default_pad_root())
    pad.create("standup", "ship the node\n")
    written = pad.save()

    dest = Path(pad.root)
    assert dest / "standup.txt" in written
    assert (dest / "standup.txt").read_text(encoding="utf-8") == "ship the node\n"
    tracked = git("ls-files", cwd=dest).stdout.splitlines()
    assert "standup.txt" in tracked
    remote = git("ls-tree", "-r", "--name-only", "HEAD", cwd=origin).stdout.splitlines()
    assert "standup.txt" in remote


def test_command_writer_emits_only_canonical_fields() -> None:
    fields = wire.command_fields(
        action=wire.ACTION_CREATE, title="standup", text=""
    )
    assert set(fields) == set(NOTEPAD_COMMAND_CANONICAL_FIELDS)
    assert all(isinstance(v, str) for v in fields.values())


def test_command_parser_rejects_an_empty_create() -> None:
    with pytest.raises(ValueError, match="title"):
        wire.command_fields(action=wire.ACTION_CREATE, title="  ")


def test_apply_command_creates_and_appends() -> None:
    pad = Pad()
    apply_command(
        pad,
        wire.parse_command(
            wire.command_fields(action=wire.ACTION_CREATE, title="standup")
        ),
    )
    apply_command(
        pad,
        wire.parse_command(
            wire.command_fields(
                action=wire.ACTION_APPEND, title="standup", text="hello"
            )
        ),
    )
    assert pad.documents["standup"].text == "hello"


# --- the node on the canvas ------------------------------------------------


def _instance(driver):
    from notepad_frontend import app as notepad_app

    live = driver.live_instance(notepad_app._LIVE)
    assert live is not None, "Notepad FE did not register itself"
    return live


@pytest.mark.canvas
def test_plus_opens_a_tab_and_typing_lands_on_it(harness) -> None:
    driver = harness.drop(NODE)
    driver.type_into("new_name", "standup")
    harness.pump(2)

    assert driver.exists("tab_standup")
    pad = _instance(driver).pad
    assert pad.current == "standup"

    driver.type_into("body", "ship the node")
    assert pad.documents["standup"].text == "ship the node"


@pytest.mark.canvas
def test_tabs_switch_the_target_document(harness) -> None:
    driver = harness.drop(NODE)
    live = _instance(driver)
    live.new_document("standup")
    live.add_text("morning")
    live.new_document("ideas")
    live.add_text("later")
    harness.pump(2)

    assert driver.exists("tab_standup")
    assert driver.exists("tab_ideas")
    assert live.pad.current == "ideas"
    assert driver.get("body") == "later"

    driver.click("tab_standup")
    harness.pump(2)
    assert live.pad.current == "standup"
    assert driver.get("body") == "morning"
    harness.screenshot("notepad-tabs")


@pytest.mark.canvas
@pytest.mark.redis
@pytest.mark.git
def test_save_git_includes_the_txt_file(
    harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    origin = tmp_path / "notes.git"
    seed = tmp_path / "seed"
    seed.mkdir()
    git("init", "-b", "dev", ".", cwd=seed)
    (seed / "README.md").write_text("notes\n", encoding="utf-8")
    git("add", "README.md", cwd=seed)
    git("commit", "-m", "seed", cwd=seed)
    git("clone", "--bare", str(seed), str(origin), cwd=tmp_path)

    monkeypatch.setenv("NOTEPAD_ROOT", str(tmp_path / "Pad"))
    driver = harness.drop(NODE)
    driver.type_into("git_url", str(origin))
    harness.wait_until(
        lambda: driver.get("status_text") == origin.stem
        or _instance(driver).pad.root is not None,
        message="Notepad to finish cloning",
    )

    live = _instance(driver)
    live.new_document("standup")
    live.add_text("ship the node")
    driver.click("save_btn")
    harness.pump(2)

    dest = Path(live.pad.root)
    assert (dest / "standup.txt").read_text(encoding="utf-8") == "ship the node"
    tracked = git("ls-files", cwd=dest).stdout.splitlines()
    assert "standup.txt" in tracked


@pytest.mark.canvas
@pytest.mark.redis
def test_a_voice_command_opens_a_tab(harness, redis_client) -> None:
    driver = harness.drop(NODE)
    redis_client.xadd(
        wire.COMMAND_STREAM,
        wire.command_fields(action=wire.ACTION_CREATE, title="standup"),
    )
    redis_client.xadd(
        wire.COMMAND_STREAM,
        wire.command_fields(
            action=wire.ACTION_APPEND, title="standup", text="from voice"
        ),
    )
    harness.wait_until(
        lambda: driver.exists("tab_standup"),
        message="Notepad to apply NOTEPAD:COMMAND",
    )
    live = _instance(driver)
    assert live.pad.documents["standup"].text == "from voice"
    assert driver.get("body") == "from voice"


@pytest.mark.canvas
def test_capture_writes_the_git_url_into_the_graph(harness, tmp_path: Path) -> None:
    import json

    import dearpygui.dearpygui as dpg
    from engine.graph_bar import CAPTURE_TAG
    from megadesk_contracts.testing import invoke_callback

    driver = harness.drop(NODE)
    driver.type_into("git_url", "https://github.com/acme/notes")
    invoke_callback(dpg.get_item_callback(CAPTURE_TAG), CAPTURE_TAG, None, None)

    saved = json.loads((tmp_path / "graph.json").read_text(encoding="utf-8"))
    assert saved["members"][driver.member_id]["parameters"]["GIT_URL"] == (
        "https://github.com/acme/notes"
    )
