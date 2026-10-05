import secrets
import string
from datetime import datetime, timedelta, timezone

from fastapi import Depends, Request
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.errors import ApiError
from app.models import AccessToken

_TOKEN_ALPHABET = string.ascii_letters + string.digits
_UNAUTHORIZED_HEADERS = {"WWW-Authenticate": "Bearer"}


def issue_token(db: Session, client_id: str) -> AccessToken:
    now = datetime.now(timezone.utc)
    db.execute(delete(AccessToken).where(AccessToken.expires_at < now))
    token = AccessToken(
        token="".join(secrets.choice(_TOKEN_ALPHABET) for _ in range(32)),
        client_id=client_id,
        expires_at=now + timedelta(seconds=get_settings().token_ttl_seconds),
    )
    db.add(token)
    db.commit()
    return token


def require_token(request: Request, db: Session = Depends(get_db)) -> AccessToken:
    """Dépendance qui exige un header `Authorization: Bearer <token>` valide et non expiré."""
    header = request.headers.get("Authorization", "")
    scheme, _, value = header.partition(" ")
    if not header:
        raise ApiError(401, "Missing Authorization header", _UNAUTHORIZED_HEADERS)
    if scheme.lower() != "bearer" or not value.strip():
        raise ApiError(401, "Authorization header must be 'Bearer <token>'", _UNAUTHORIZED_HEADERS)

    token = db.scalar(select(AccessToken).where(AccessToken.token == value.strip()))
    if token is None:
        raise ApiError(401, "Invalid access token", _UNAUTHORIZED_HEADERS)
    expires_at = token.expires_at
    if expires_at.tzinfo is None:  # SQLite ne conserve pas le fuseau horaire
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        raise ApiError(401, "Access token expired", _UNAUTHORIZED_HEADERS)
    return token
