import secrets

from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.errors import OAuthError
from app.schemas import TokenRequest
from app.security import issue_token

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/oauth2/token")
async def create_token(request: Request, db: Session = Depends(get_db)):
    """Accepte le corps en JSON (format de la spec) ou en form-urlencoded (standard OAuth2)."""
    payload = await _read_payload(request)
    settings = get_settings()

    if payload.grant_type != "client_credentials":
        raise OAuthError(400, "unsupported_grant_type", "grant_type must be 'client_credentials'")
    if not payload.client_id or not payload.client_secret:
        raise OAuthError(400, "invalid_request", "client_id and client_secret are required")
    valid_id = secrets.compare_digest(payload.client_id, settings.client_id)
    valid_secret = secrets.compare_digest(payload.client_secret, settings.client_secret)
    if not (valid_id and valid_secret):
        raise OAuthError(401, "invalid_client", "Invalid client credentials")

    token = issue_token(db, payload.client_id)
    return {
        "access_token": token.token,
        "expires_in": settings.token_ttl_seconds,
        "token_type": "bearer",
    }


async def _read_payload(request: Request) -> TokenRequest:
    content_type = request.headers.get("content-type", "")
    try:
        if content_type.startswith("application/x-www-form-urlencoded"):
            data = dict(await request.form())
        else:
            data = await request.json()
        if not isinstance(data, dict):
            raise ValueError("body must be an object")
        return TokenRequest.model_validate(data)
    except (ValueError, ValidationError):
        raise OAuthError(400, "invalid_request", "Malformed request body")
