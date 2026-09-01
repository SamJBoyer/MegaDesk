"""VisionBoard: board geometry, and the seam between the board and the graph.

The geometry half needs no desktop session — ``board.py`` imports no Dear PyGui.
The canvas half boots the real canvas and calls the same coordinate entry points
the mouse handlers call, which is the only way to press a drawlist: there is no
widget under a sticky for ``NodeDriver`` to click.
"""

from __future__ import annotations

import json
from pathlib import Path

from megadesk_contracts import host as dpg
import pytest
from megadesk_contracts.testing import CanvasHarness, invoke_callback

from vision_board_frontend import app as vision_board_app
from vision_board_frontend.board import (
    CONTAINER_HEADER,
    FONT_MAX,
    FONT_MIN,
    NOTE_SIZE,
    Board,
    Camera,
    dump_board,
    fit_text,
    load_board,
    wrap_lines,
)

NODE = "vision_board"


# --- board geometry --------------------------------------------------------


def test_a_sticky_is_centred_on_the_point_it_was_placed_at() -> None:
    board = Board()
    note = board.add_note(300.0, 200.0)
    assert note.center() == (300.0, 200.0)
    assert board.note_at(300.0, 200.0) is note
    assert board.note_at(300.0 + NOTE_SIZE, 200.0) is None


def test_the_topmost_sticky_takes_the_click() -> None:
    board = Board()
    board.add_note(100.0, 100.0)
    top = board.add_note(120.0, 100.0)
    assert board.note_at(120.0, 100.0) is top


def test_a_container_is_grabbed_by_its_frame_not_its_hollow_middle() -> None:
    """The inside must stay click-through, or the stickies in it are unreachable."""
    board = Board()
    container = board.add_container(0.0, 0.0, 400.0, 300.0)

    assert board.container_at(200.0, CONTAINER_HEADER / 2) is container
    assert board.container_at(1.0, 150.0) is container
    assert board.container_at(200.0, 299.0) is container
    assert board.container_at(200.0, 150.0) is None


def test_a_container_carries_only_the_stickies_whose_centre_it_covers() -> None:
    board = Board()
    container = board.add_container(0.0, 0.0, 400.0, 300.0)
    inside = board.add_note(200.0, 150.0)
    board.add_note(600.0, 150.0)
    # Overlaps the right edge, but its centre is outside — the frame leaves it.
    straddling = board.add_note(420.0, 150.0)

    held = board.notes_in(container)
    assert inside in held
    assert straddling not in held
    assert len(held) == 1


def test_wrapping_breaks_on_words_and_hard_splits_one_that_cannot_fit() -> None:
    assert wrap_lines("alpha beta gamma", 11) == ["alpha beta", "gamma"]
    assert wrap_lines("supercalifragilistic", 6) == [
        "superc",
        "alifra",
        "gilist",
        "ic",
    ]


def test_note_text_shrinks_until_it_fits_inside_the_sticky() -> None:
    short_size, short_lines = fit_text("hi")
    assert short_size == FONT_MAX
    assert short_lines == ["hi"]

    long_size, long_lines = fit_text("ship it " * 24)
    assert FONT_MIN <= long_size < short_size
    assert long_lines
    assert len(long_lines) * long_size * 1.15 <= NOTE_SIZE


def test_text_too_long_even_at_the_smallest_size_is_clipped_to_the_sticky() -> None:
    size, lines = fit_text("word " * 400)
    assert size == FONT_MIN
    assert len(lines) * size * 1.15 <= NOTE_SIZE


def test_zooming_keeps_the_world_point_under_the_cursor_pinned() -> None:
    camera = Camera(x=120.0, y=80.0, zoom=1.0)
    before = camera.to_world(210.0, 130.0)
    camera.zoom_at(210.0, 130.0, 3)
    after = camera.to_world(210.0, 130.0)

    assert camera.zoom > 1.0
    assert after[0] == pytest.approx(before[0])
    assert after[1] == pytest.approx(before[1])


def test_zoom_is_clamped_at_both_ends() -> None:
    camera = Camera()
    camera.zoom_at(0.0, 0.0, 200)
    assert camera.zoom == pytest.approx(3.0)
    camera.zoom_at(0.0, 0.0, -400)
    assert camera.zoom == pytest.approx(0.3)


def test_a_board_round_trips_through_its_two_parameters() -> None:
    board = Board()
    note = board.add_note(140.0, 90.0, text="ship the node")
    container = board.add_container(0.0, 0.0, 320.0, 240.0, name="Now")

    restored = load_board(dump_board(board))

    assert [n.as_dict() for n in restored.notes] == [note.as_dict()]
    assert [c.as_dict() for c in restored.containers] == [container.as_dict()]


def test_a_hand_edited_parameter_that_is_not_a_board_loads_empty() -> None:
    """Graph files are user-editable, so junk must not take the node down."""
    board = load_board({"NOTES": "{oops", "CONTAINERS": '{"not": "a list"}'})
    assert board.notes == []
    assert board.containers == []


# --- the node on the canvas ------------------------------------------------


def _instance(driver):
    live = driver.live_instance(vision_board_app._LIVE)
    assert live is not None, "VisionBoard FE did not register itself"
    return live


@pytest.mark.canvas
def test_mb3_places_a_sticky_and_capture_writes_it_into_the_graph(
    harness, tmp_path: Path
) -> None:
    from engine.graph_bar import CAPTURE_TAG

    driver = harness.drop(NODE)
    board = _instance(driver)

    note_id = board.place_note(150.0, 110.0)
    assert note_id is not None
    harness.pump(2)
    assert driver.exists(f"note_{note_id}"), "the sticky was never drawn"

    invoke_callback(dpg.get_item_callback(CAPTURE_TAG), CAPTURE_TAG, None, None)

    saved = json.loads((tmp_path / "graph.json").read_text(encoding="utf-8"))
    notes = json.loads(saved["members"][driver.member_id]["parameters"]["NOTES"])
    assert [n["id"] for n in notes] == [note_id]
    assert notes[0]["x"] == pytest.approx(150.0 - NOTE_SIZE / 2)


@pytest.mark.canvas
def test_mb3_on_an_occupied_spot_does_not_stack_a_second_sticky(harness) -> None:
    driver = harness.drop(NODE)
    board = _instance(driver)

    board.place_note(150.0, 110.0)
    assert board.place_note(150.0, 110.0) is None
    assert len(board.board.notes) == 1


@pytest.mark.canvas
def test_double_clicking_a_sticky_opens_the_editor_and_typing_lands_on_it(
    harness,
) -> None:
    driver = harness.drop(NODE)
    board = _instance(driver)
    note_id = board.place_note(150.0, 110.0)

    assert not driver.shown("edit")
    assert board.open_editor(150.0, 110.0) == ("note", note_id)
    assert driver.shown("edit")

    driver.type_into("edit", "ship the node")
    assert board.board.note(note_id).text == "ship the node"

    # Pressing the board commits and puts the zoom readout back.
    board.press(20.0, 20.0)
    assert not driver.shown("edit")
    assert driver.shown("zoom_lbl")


@pytest.mark.canvas
def test_dragging_a_container_moves_the_stickies_inside_it(harness) -> None:
    driver = harness.drop(NODE)
    board = _instance(driver)

    inside_id = board.place_note(120.0, 120.0)
    outside_id = board.place_note(380.0, 120.0)

    driver.click("frame_btn")
    board.press(40.0, 40.0)
    board.drag(200.0, 160.0)
    container_id = board.release()
    assert container_id is not None
    harness.pump(2)
    assert driver.exists(f"container_{container_id}")

    inside = board.board.note(inside_id)
    outside = board.board.note(outside_id)
    inside_before = (inside.x, inside.y)
    outside_before = (outside.x, outside.y)

    board.press(60.0, 40.0 + CONTAINER_HEADER / 2)
    board.drag(30.0, 45.0)
    board.release()

    assert (inside.x, inside.y) == pytest.approx(
        (inside_before[0] + 30.0, inside_before[1] + 45.0)
    )
    assert (outside.x, outside.y) == pytest.approx(outside_before)


@pytest.mark.canvas
def test_the_container_tool_disarms_after_one_frame(harness) -> None:
    driver = harness.drop(NODE)
    board = _instance(driver)

    driver.click("frame_btn")
    board.press(40.0, 40.0)
    board.drag(200.0, 160.0)
    board.release()

    # Well clear of the frame just drawn, so this is empty board.
    board.press(320.0, 250.0)
    board.drag(25.0, 0.0)
    board.release()

    assert len(board.board.containers) == 1, "the second drag drew another frame"
    assert board.camera.x == pytest.approx(-25.0), "the second drag did not pan"


@pytest.mark.canvas
def test_dragging_the_board_pans_and_the_wheel_zooms(harness) -> None:
    driver = harness.drop(NODE)
    board = _instance(driver)

    board.press(200.0, 150.0)
    board.drag(-60.0, -20.0)
    board.release()
    assert (board.camera.x, board.camera.y) == pytest.approx((60.0, 20.0))

    board.zoom(200.0, 150.0, 2)
    assert board.camera.zoom > 1.0
    assert driver.get("zoom_lbl") == f"{round(board.camera.zoom * 100):d}%"


@pytest.mark.canvas
def test_a_saved_board_reopens_with_its_stickies_and_containers(
    tmp_path: Path, artifacts_dir: Path, fast_polling: None
) -> None:
    path = tmp_path / "graph.json"
    path.write_text(
        json.dumps(
            {
                "members": {
                    "vb-1": {
                        "member_id": "vb-1",
                        "type": "megadesk",
                        "node_name": NODE,
                        "position": [40.0, 40.0],
                        "parameters": {
                            "NOTES": json.dumps(
                                [{"id": "n1", "x": 40.0, "y": 60.0, "text": "ship it"}]
                            ),
                            "CONTAINERS": json.dumps(
                                [
                                    {
                                        "id": "c1",
                                        "x": 20.0,
                                        "y": 20.0,
                                        "w": 260.0,
                                        "h": 200.0,
                                        "name": "Now",
                                    }
                                ]
                            ),
                        },
                        "data": {"width": 440.0, "height": 320.0, "node_name": NODE},
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    with CanvasHarness(
        graph_path=path, artifacts_dir=artifacts_dir, supervisor_panel=False
    ) as harness:
        driver = harness.driver_for(NODE)
        board = _instance(driver)

        assert driver.exists("note_n1")
        assert driver.exists("container_c1")
        assert board.board.note("n1").text == "ship it"
        assert board.board.container("c1").name == "Now"
