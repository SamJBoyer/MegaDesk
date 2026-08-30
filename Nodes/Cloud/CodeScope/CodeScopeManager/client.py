"""HTTP client for the CodeScope cloud node.

VoiceDeck and the canvas FE are clients of a process that is not on this
machine. ``CODESCOPE_URL`` + ``CODESCOPE_API_TOKEN`` are the only wiring.
Tests inject a transport (FastAPI ``TestClient``) or ``FakeCodeScopeClient``.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Iterator, Optional

from megadesk_contracts.wire import code_scope as wire

URL_ENV = "CODESCOPE_URL"
TOKEN_ENV = "CODESCOPE_API_TOKEN"


class CodeScopeError(RuntimeError):
    """The HTTP service refused or could not complete a call."""

    def __init__(self, message: str, *, status_code: int = 0) -> None:
        super().__init__(message)
        self.status_code = int(status_code or 0)


class CodeScopeClient:
    """GET /repos, POST /repos, POST /sessions/{id}/ask (SSE)."""

    def __init__(
        self,
        *,
        base_url: str = "",
        token: str = "",
        transport: Any = None,
    ) -> None:
        self.base_url = str(base_url or "").strip().rstrip("/")
        self.token = str(token or "").strip()
        self._transport = transport

    @classmethod
    def from_env(cls) -> "CodeScopeClient":
        return cls(
            base_url=os.environ.get(URL_ENV, ""),
            token=os.environ.get(TOKEN_ENV, ""),
        )

    def configured(self) -> bool:
        return bool(self.base_url and self.token) or self._transport is not None

    def health(self) -> bool:
        try:
            body = self._request("GET", "/health", auth=False)
        except CodeScopeError:
            return False
        return bool(isinstance(body, dict) and body.get("ok"))

    def list_repos(self) -> list[dict[str, str]]:
        body = self._request("GET", "/repos")
        repos = body.get("repos") if isinstance(body, dict) else None
        if not isinstance(repos, list):
            return []
        return [dict(item) for item in repos if isinstance(item, dict)]

    def open_repo(self, url: str, *, model: str = "") -> dict[str, str]:
        payload: dict[str, str] = {"url": str(url)}
        if model:
            payload["model"] = str(model)
        body = self._request("POST", "/repos", json_body=payload)
        if not isinstance(body, dict):
            raise CodeScopeError("open_repo returned a non-object")
        return {str(key): str(value) for key, value in body.items()}

    def get_session(self, session_id: str) -> dict[str, str]:
        body = self._request("GET", f"/sessions/{session_id}")
        if not isinstance(body, dict):
            raise CodeScopeError("get_session returned a non-object")
        return {str(key): str(value) for key, value in body.items()}

    def sync(self, session_id: str) -> dict[str, str]:
        body = self._request("POST", f"/sessions/{session_id}/sync")
        if not isinstance(body, dict):
            raise CodeScopeError("sync returned a non-object")
        return {str(key): str(value) for key, value in body.items()}

    def ask(
        self,
        session_id: str,
        question: str,
        *,
        mode: str = wire.MODE_ANSWER,
        question_id: str = "",
    ) -> Iterator[dict[str, str]]:
        payload: dict[str, str] = {
            "question": str(question),
            "mode": str(mode or wire.MODE_ANSWER),
        }
        if question_id:
            payload["question_id"] = str(question_id)
        path = f"/sessions/{session_id}/ask"
        for raw in self._stream("POST", path, json_body=payload):
            try:
                parsed = wire.parse_answer(raw)
            except ValueError:
                continue
            yield parsed

    def _headers(self, *, auth: bool = True, sse: bool = False) -> dict[str, str]:
        headers = {"Accept": "text/event-stream" if sse else "application/json"}
        if auth and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[dict[str, str]] = None,
        auth: bool = True,
    ) -> Any:
        if self._transport is not None:
            return self._transport_json(method, path, json_body=json_body, auth=auth)
        if not self.configured():
            raise CodeScopeError(
                f"{URL_ENV} and {TOKEN_ENV} must be set to reach CodeScope"
            )
        data = None if json_body is None else json.dumps(json_body).encode("utf-8")
        headers = self._headers(auth=auth)
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request) as response:
                raw = response.read().decode("utf-8")
                status = int(getattr(response, "status", 200) or 200)
        except urllib.error.HTTPError as exc:
            detail = _http_error_detail(exc)
            raise CodeScopeError(detail, status_code=int(exc.code or 0)) from exc
        except urllib.error.URLError as exc:
            raise CodeScopeError(f"CodeScope unreachable: {exc.reason}") from exc
        if status >= 400:
            raise CodeScopeError(raw or f"HTTP {status}", status_code=status)
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CodeScopeError("CodeScope returned non-JSON") from exc

    def _stream(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[dict[str, str]] = None,
    ) -> Iterator[dict[str, str]]:
        if self._transport is not None:
            yield from self._transport_sse(method, path, json_body=json_body)
            return
        if not self.configured():
            raise CodeScopeError(
                f"{URL_ENV} and {TOKEN_ENV} must be set to reach CodeScope"
            )
        data = json.dumps(json_body or {}).encode("utf-8")
        headers = self._headers(sse=True)
        headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request) as response:
                yield from _iter_sse_lines(response)
        except urllib.error.HTTPError as exc:
            detail = _http_error_detail(exc)
            raise CodeScopeError(detail, status_code=int(exc.code or 0)) from exc
        except urllib.error.URLError as exc:
            raise CodeScopeError(f"CodeScope unreachable: {exc.reason}") from exc

    def _transport_json(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[dict[str, str]],
        auth: bool,
    ) -> Any:
        headers = self._headers(auth=auth)
        response = self._transport_call(method, path, json_body=json_body, headers=headers)
        status = int(getattr(response, "status_code", 200) or 200)
        if status >= 400:
            raise CodeScopeError(_response_detail(response), status_code=status)
        try:
            return response.json()
        except Exception:
            return {}

    def _transport_sse(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[dict[str, str]],
    ) -> Iterator[dict[str, str]]:
        headers = self._headers(sse=True)
        stream = getattr(self._transport, "stream", None)
        if callable(stream):
            with stream(method, path, json=json_body, headers=headers) as response:
                status = int(getattr(response, "status_code", 200) or 200)
                if status >= 400:
                    raise CodeScopeError(_response_detail(response), status_code=status)
                for line in response.iter_lines():
                    payload = _sse_data_line(line)
                    if payload is not None:
                        yield payload
            return
        response = self._transport_call(method, path, json_body=json_body, headers=headers)
        status = int(getattr(response, "status_code", 200) or 200)
        if status >= 400:
            raise CodeScopeError(_response_detail(response), status_code=status)
        text = getattr(response, "text", "") or ""
        for block in text.split("\n\n"):
            payload = _sse_data_line(block)
            if payload is not None:
                yield payload

    def _transport_call(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[dict[str, str]],
        headers: dict[str, str],
    ) -> Any:
        verb = method.lower()
        fn = getattr(self._transport, verb, None)
        if not callable(fn):
            raise CodeScopeError(f"transport has no {verb} method")
        if json_body is None:
            return fn(path, headers=headers)
        return fn(path, json=json_body, headers=headers)


def _http_error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        raw = exc.read().decode("utf-8")
    except Exception:
        raw = ""
    if raw:
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            return raw
        if isinstance(body, dict) and body.get("detail"):
            return str(body["detail"])
    return str(exc.reason or exc)


def _response_detail(response: Any) -> str:
    try:
        body = response.json()
    except Exception:
        text = getattr(response, "text", "") or ""
        return text or f"HTTP {getattr(response, 'status_code', '?')}"
    if isinstance(body, dict) and body.get("detail"):
        return str(body["detail"])
    return str(body)


def _sse_data_line(line: Any) -> Optional[dict[str, str]]:
    if isinstance(line, bytes):
        text = line.decode("utf-8", errors="replace")
    else:
        text = str(line or "")
    for piece in text.splitlines():
        if piece.startswith("data:"):
            raw = piece[len("data:") :].strip()
            if not raw:
                return None
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                return None
            if isinstance(payload, dict):
                return {str(key): str(value) for key, value in payload.items()}
    return None


def _iter_sse_lines(response: Any) -> Iterator[dict[str, str]]:
    buf = ""
    for chunk in response:
        buf += chunk.decode("utf-8", errors="replace") if isinstance(chunk, bytes) else str(chunk)
        while "\n\n" in buf:
            block, buf = buf.split("\n\n", 1)
            payload = _sse_data_line(block)
            if payload is not None:
                yield payload
    payload = _sse_data_line(buf)
    if payload is not None:
        yield payload


# --- process-wide override so tests can inject without env -----------------

_OVERRIDE: Optional[CodeScopeClient] = None


def get_client() -> CodeScopeClient:
    if _OVERRIDE is not None:
        return _OVERRIDE
    return CodeScopeClient.from_env()


def set_client(client: Optional[CodeScopeClient]) -> None:
    global _OVERRIDE
    _OVERRIDE = client


class FakeCodeScopeClient:
    """In-memory CodeScope for VoiceDeck tests: no HTTP, no Cursor, no Redis."""

    def __init__(self) -> None:
        self.sessions: list[dict[str, str]] = []
        self.asks: list[dict[str, str]] = []
        self._answers: dict[str, list[dict[str, str]]] = {}

    def configured(self) -> bool:
        return True

    def health(self) -> bool:
        return True

    def seed_repo(
        self,
        *,
        repo: str = "widgets",
        session_id: str = "",
        url: str = "https://github.com/acme/widgets",
        status: str = wire.SESSION_READY,
        model: str = "auto",
    ) -> dict[str, str]:
        session = {
            "session_id": session_id or wire.new_session_id(),
            "repo": repo,
            "url": url,
            "status": status,
            "model": model,
        }
        self.sessions.append(session)
        return session

    def queue_answer(self, question_id: str, events: list[dict[str, str]]) -> None:
        self._answers[question_id] = list(events)

    def list_repos(self) -> list[dict[str, str]]:
        return [dict(item) for item in self.sessions]

    def open_repo(self, url: str, *, model: str = "") -> dict[str, str]:
        for session in self.sessions:
            if session.get("url") == url or session.get("repo") in url:
                return dict(session)
        return self.seed_repo(url=url, model=model or "auto")

    def get_session(self, session_id: str) -> dict[str, str]:
        for session in self.sessions:
            if session["session_id"] == session_id:
                return dict(session)
        raise CodeScopeError(f"No CodeScope session {session_id}", status_code=404)

    def sync(self, session_id: str) -> dict[str, str]:
        session = self.get_session(session_id)
        session["sha"] = "deadbeef"
        return session

    def ask(
        self,
        session_id: str,
        question: str,
        *,
        mode: str = wire.MODE_ANSWER,
        question_id: str = "",
    ) -> Iterator[dict[str, str]]:
        self.get_session(session_id)
        qid = question_id or wire.new_question_id()
        self.asks.append(
            {
                "session_id": session_id,
                "question_id": qid,
                "question": question,
                "mode": mode,
            }
        )
        events = self._answers.pop(qid, [])
        yield from events
