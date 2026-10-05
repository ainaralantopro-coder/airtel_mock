"""Journalise dans call_receive chaque appel de la banque vers les API exposées."""

import logging
import time

from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.database import SessionLocal
from app.errors import PARTNER_PATH_PREFIXES
from app.models import CallReceive

logger = logging.getLogger(__name__)


class CallReceiveMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not request.url.path.startswith(PARTNER_PATH_PREFIXES):
            return await call_next(request)

        started = time.perf_counter()
        request_body = await request.body()
        try:
            response = await call_next(request)
        except Exception:
            await self._save(request, request_body, 500, None, started)
            raise

        response_body = b"".join([chunk async for chunk in response.body_iterator])
        await self._save(request, request_body, response.status_code, response_body, started)
        return Response(
            content=response_body,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
        )

    async def _save(self, request, request_body, status, response_body, started):
        record = CallReceive(
            method=request.method,
            path=request.url.path,
            query_string=request.url.query or None,
            client_ip=request.client.host if request.client else None,
            request_headers=dict(request.headers),
            request_body=_decode(request_body),
            response_status=status,
            response_body=_decode(response_body),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        try:
            await run_in_threadpool(_insert, record)
        except Exception:
            logger.exception("Impossible d'enregistrer l'appel entrant dans call_receive")


def _insert(record: CallReceive) -> None:
    with SessionLocal() as db:
        db.add(record)
        db.commit()


def _decode(body: bytes | None) -> str | None:
    if not body:
        return None
    return body.decode("utf-8", errors="replace")
