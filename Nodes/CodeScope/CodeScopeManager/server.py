"""HTTP surface for CodeScope: repos in, streamed answers out.

No Redis. Auth is a shared bearer token (``CODESCOPE_API_TOKEN``). ``/health``
is the only unauthenticated route so a load balancer can probe without the
secret.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Iterator, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from megadesk_contracts import CloneError
from megadesk_contracts.wire import code_scope as wire

from CodeScopeManager.service import ScopeService, public_session

log = logging.getLogger("code_scope.server")

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8080
TOKEN_ENV = "CODESCOPE_API_TOKEN"


class RepoIn(BaseModel):
    url: str
    model: str = ""


class AskIn(BaseModel):
    question: str
    mode: str = Field(default=wire.MODE_ANSWER)


def _bearer_token(authorization: Optional[str]) -> str:
    prefix = "Bearer "
    text = (authorization or "").strip()
    if not text.startswith(prefix):
        return ""
    return text[len(prefix) :].strip()


def create_app(
    *,
    service: Optional[ScopeService] = None,
    api_token: str,
) -> FastAPI:
    """Build the app. ``api_token`` is required; tests pass it explicitly."""
    token = str(api_token or "").strip()
    if not token:
        raise ValueError(f"{TOKEN_ENV} is required")
    scoped = service or ScopeService()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        scoped.close()

    app = FastAPI(title="CodeScope", lifespan=lifespan)
    app.state.service = scoped
    app.state.api_token = token

    def require_token(
        authorization: Optional[str] = Header(default=None),
    ) -> None:
        given = _bearer_token(authorization)
        if not given or not hmac.compare_digest(given, token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid or missing bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    @app.get("/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/repos", dependencies=[Depends(require_token)])
    def list_repos() -> dict[str, Any]:
        return {"repos": scoped.list_sessions()}

    @app.post("/repos", dependencies=[Depends(require_token)])
    def open_repo(body: RepoIn) -> dict[str, str]:
        try:
            session = scoped.open_repo(body.url, model=body.model)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
            ) from exc
        except CloneError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
            ) from exc
        return public_session(session)

    @app.get("/sessions/{session_id}", dependencies=[Depends(require_token)])
    def get_session(session_id: str) -> dict[str, str]:
        try:
            return scoped.get_session(session_id)
        except KeyError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No CodeScope session {session_id}",
            )

    @app.post("/sessions/{session_id}/ask", dependencies=[Depends(require_token)])
    def ask(session_id: str, body: AskIn) -> StreamingResponse:
        question = (body.question or "").strip()
        if not question:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="no question was provided",
            )
        try:
            scoped.get_session(session_id)
        except KeyError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No CodeScope session {session_id}",
            )

        def events() -> Iterator[str]:
            for payload in scoped.ask(session_id, question, mode=body.mode):
                yield f"data: {json.dumps(payload)}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    return app


def resolve_api_token(explicit: Optional[str] = None) -> str:
    token = (explicit if explicit is not None else os.environ.get(TOKEN_ENV, "")).strip()
    if not token:
        raise SystemExit(
            f"{TOKEN_ENV} is required. Generate one (a long random string) and "
            "export it before starting the server."
        )
    return token


def serve(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    service: Optional[ScopeService] = None,
    api_token: Optional[str] = None,
) -> None:
    import uvicorn

    token = resolve_api_token(api_token)
    app = create_app(service=service, api_token=token)
    log.info("CodeScope HTTP listening on %s:%s", host, port)
    uvicorn.run(app, host=host, port=port)
