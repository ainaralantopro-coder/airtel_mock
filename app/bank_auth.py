"""Token OAuth2 (client_credentials, Keycloak) mis dans le header Authorization des appels vers la banque.

Le token est demandé au premier appel puis réutilisé jusqu'à peu avant son expiration (expires_in), un seul
pour tous les threads. Sans BANK_TOKEN_URL, les appels partent sans Authorization (bank-mock local).
"""

import threading
import time

import httpx

from app.config import get_settings

# Marge avant expiration : un token ne doit pas expirer entre son envoi et son contrôle par la banque
EXPIRY_MARGIN_SECONDS = 30

_lock = threading.Lock()
_token: str | None = None
_expires_at = 0.0  # time.monotonic()


class TokenError(Exception):
    """Token impossible à obtenir : l'appel à la banque n'est pas envoyé."""


def authorization_header() -> str | None:
    """Valeur du header Authorization ("Bearer <token>"), ou None si aucun BANK_TOKEN_URL n'est configuré."""
    global _token, _expires_at
    settings = get_settings()
    if not settings.bank_token_url:
        return None
    with _lock:
        if _token is None or time.monotonic() >= _expires_at:
            _token, lifetime = _request_token()
            _expires_at = time.monotonic() + max(lifetime - EXPIRY_MARGIN_SECONDS, lifetime / 2)
        return f"Bearer {_token}"


def invalidate() -> None:
    """Oublie le token en cache (refusé par la banque) : le prochain appel en demande un nouveau."""
    global _token
    with _lock:
        _token = None


def _request_token() -> tuple[str, float]:
    settings = get_settings()
    data = {
        "grant_type": "client_credentials",
        "client_id": settings.bank_token_client_id,
        "client_secret": settings.bank_token_client_secret,
    }
    if settings.bank_token_scope:
        data["scope"] = settings.bank_token_scope
    try:
        with httpx.Client(timeout=settings.bank_timeout_seconds) as client:
            response = client.post(settings.bank_token_url, data=data)
    except httpx.HTTPError as exc:
        raise TokenError(f"{type(exc).__name__}: {exc}") from None
    try:
        body = response.json()
    except ValueError:
        body = None
    if response.status_code != 200 or not isinstance(body, dict) or not body.get("access_token"):
        detail = body.get("error_description") or body.get("error") if isinstance(body, dict) else None
        raise TokenError(f"HTTP {response.status_code}" + (f": {detail}" if detail else ""))
    try:
        lifetime = float(body.get("expires_in") or 60)
    except (TypeError, ValueError):
        lifetime = 60.0
    return str(body["access_token"]), lifetime
