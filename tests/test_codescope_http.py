"""CodeScope HTTP service: clone a repo, ask, read streamed sentences.

No canvas, no Redis, no Cursor key. ``FakeCodeAgent.runner_factory`` stands in
for ``cursor_sdk``; git clone and sentence buffering are real.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from megadesk_contracts.testing import FakeCodeAgent
from megadesk_contracts.wire import code_scope as wire

from CodeScopeManager.server import create_app
from CodeScopeManager.service import ScopeService

TOKEN = "test-codescope-token"
QUESTION = "Why does the frame pump need a reset?"
ANSWER = (
    "The pump is a module global that outlives the Dear PyGui context. "
    "Whoever owns the context resets it on teardown."
)

AUTH = {"Authorization": f"Bearer {TOKEN}"}


def parse_sse(body: str) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    for block in body.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data:"):
                events.append(json.loads(line[len("data:") :].strip()))
    return events


def make_client(
    tmp_path: Path, fake: FakeCodeAgent | None = None
) -> tuple[TestClient, ScopeService, FakeCodeAgent]:
    agent = fake or FakeCodeAgent(redis=None)
    service = ScopeService(
        root=tmp_path / "Scope",
        runner_factory=agent.runner_factory,
    )
    app = create_app(service=service, api_token=TOKEN)
    return TestClient(app), service, agent


def test_health_is_public(tmp_path: Path) -> None:
    client, _service, _agent = make_client(tmp_path)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_repos_without_a_token_is_unauthorized(tmp_path: Path) -> None:
    client, _service, _agent = make_client(tmp_path)
    assert client.get("/repos").status_code == 401
    assert client.post("/repos", json={"url": "https://github.com/acme/widgets"}).status_code == 401


def test_a_wrong_token_is_unauthorized(tmp_path: Path) -> None:
    client, _service, _agent = make_client(tmp_path)
    response = client.get(
        "/repos", headers={"Authorization": "Bearer not-the-token"}
    )
    assert response.status_code == 401


def test_an_unrecognized_url_is_rejected(tmp_path: Path) -> None:
    client, _service, _agent = make_client(tmp_path)
    response = client.post(
        "/repos", json={"url": "not a repo at all"}, headers=AUTH
    )
    assert response.status_code == 400
    assert "Unrecognized" in response.json()["detail"]


@pytest.mark.git
def test_opening_a_repo_clones_it_and_asking_streams_sentences(
    tmp_path: Path, origin_repo: Path
) -> None:
    client, service, agent = make_client(tmp_path)
    agent.add_answer("frame pump", ANSWER)

    opened = client.post(
        "/repos", json={"url": str(origin_repo)}, headers=AUTH
    )
    assert opened.status_code == 200, opened.text
    body = opened.json()
    assert body["repo"] == "widgets"
    assert body["status"] == wire.SESSION_READY
    session_id = body["session_id"]
    assert (tmp_path / "Scope" / "widgets" / ".git").exists()

    listed = client.get("/repos", headers=AUTH)
    assert listed.status_code == 200
    assert listed.json()["repos"] == [
        {
            "session_id": session_id,
            "repo": "widgets",
            "url": str(origin_repo),
            "status": wire.SESSION_READY,
            "model": "auto",
        }
    ]

    asked = client.post(
        f"/sessions/{session_id}/ask",
        json={"question": QUESTION},
        headers=AUTH,
    )
    assert asked.status_code == 200
    assert asked.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(asked.text)
    assert [event["final"] for event in events] == ["false", "true"]
    assert events[0]["answer"].startswith("The pump is a module global")
    assert events[1]["answer"].startswith("Whoever owns the context")
    assert all(event["status"] == wire.STATUS_OK for event in events)
    assert all(event["question_id"] for event in events)
    assert all(event["session_id"] == session_id for event in events)
    assert agent.questions == [QUESTION]
    assert service.store.get(session_id)["agent_id"] == agent.agent_id
    assert service.store.get(session_id)["status"] == wire.SESSION_READY


@pytest.mark.git
def test_the_http_client_opens_a_repo_and_streams_an_ask(
    tmp_path: Path, origin_repo: Path
) -> None:
    from CodeScopeManager.client import CodeScopeClient

    transport, _service, agent = make_client(tmp_path)
    agent.add_answer("frame pump", ANSWER)
    client = CodeScopeClient(token=TOKEN, transport=transport)
    opened = client.open_repo(str(origin_repo))
    events = list(client.ask(opened["session_id"], QUESTION))
    assert [event["final"] for event in events] == [False, True]
    assert events[0]["answer"].startswith("The pump is a module global")
    assert agent.questions == [QUESTION]


@pytest.mark.git
def test_opening_the_same_repo_twice_reuses_the_session(
    tmp_path: Path, origin_repo: Path
) -> None:
    client, _service, _agent = make_client(tmp_path)
    first = client.post("/repos", json={"url": str(origin_repo)}, headers=AUTH)
    second = client.post("/repos", json={"url": str(origin_repo)}, headers=AUTH)
    assert first.json()["session_id"] == second.json()["session_id"]


@pytest.mark.git
def test_sessions_json_survives_a_restart(
    tmp_path: Path, origin_repo: Path
) -> None:
    client, service, _agent = make_client(tmp_path)
    opened = client.post("/repos", json={"url": str(origin_repo)}, headers=AUTH)
    session_id = opened.json()["session_id"]
    client.close()
    service.close()

    restarted = ScopeService(root=tmp_path / "Scope")
    assert restarted.get_session(session_id)["repo"] == "widgets"


def test_an_unknown_session_is_not_found(tmp_path: Path) -> None:
    client, _service, _agent = make_client(tmp_path)
    response = client.post(
        "/sessions/never-existed/ask",
        json={"question": QUESTION},
        headers=AUTH,
    )
    assert response.status_code == 404
    assert "never-existed" in response.json()["detail"]


@pytest.mark.git
def test_an_empty_question_is_rejected(
    tmp_path: Path, origin_repo: Path
) -> None:
    client, _service, _agent = make_client(tmp_path)
    opened = client.post("/repos", json={"url": str(origin_repo)}, headers=AUTH)
    session_id = opened.json()["session_id"]
    response = client.post(
        f"/sessions/{session_id}/ask",
        json={"question": "  "},
        headers=AUTH,
    )
    assert response.status_code == 400


@pytest.mark.git
def test_an_agent_that_cannot_start_streams_a_final_error(
    tmp_path: Path, origin_repo: Path
) -> None:
    client, service, agent = make_client(tmp_path)
    agent.startup_error = "no CURSOR_API_KEY in the environment"
    opened = client.post("/repos", json={"url": str(origin_repo)}, headers=AUTH)
    session_id = opened.json()["session_id"]

    asked = client.post(
        f"/sessions/{session_id}/ask",
        json={"question": QUESTION},
        headers=AUTH,
    )
    events = parse_sse(asked.text)
    assert len(events) == 1
    assert events[0]["status"] == wire.STATUS_ERROR
    assert events[0]["final"] == "true"
    assert "CURSOR_API_KEY" in events[0]["answer"]
    assert service.store.get(session_id)["status"] == wire.SESSION_ERROR
