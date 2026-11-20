import json
from urllib.parse import parse_qs

import httpx
import pytest
from sqlalchemy import select

from app import bank_auth, bank_client
from app.config import get_settings
from app.database import SessionLocal
from app.models import CallSent

TOKEN_URL = "http://keycloak.test/realms/pamf/protocol/openid-connect/token"
MINI_STATEMENT_URL = "http://bank.test/mini-statement"


@pytest.fixture
def bank(monkeypatch):
    """Keycloak et banque simulés. `bank` : réponses successives de la banque ; `token` : réponse de Keycloak."""
    settings = get_settings()
    monkeypatch.setattr(settings, "bank_token_url", TOKEN_URL)
    monkeypatch.setattr(settings, "bank_token_client_id", "airtel-am-loan")
    monkeypatch.setattr(settings, "bank_token_client_secret", "secret")
    monkeypatch.setattr(settings, "bank_token_scope", "pamf.read")
    bank_auth.invalidate()

    state = {"token_requests": [], "authorizations": [], "bank": [], "issued": 0,
             "token": lambda n: (200, {"access_token": f"tok{n}", "expires_in": 300, "token_type": "Bearer"})}

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == TOKEN_URL:
            state["token_requests"].append(parse_qs(request.content.decode()))
            state["issued"] += 1
            status, body = state["token"](state["issued"])
            return httpx.Response(status, json=body)
        state["authorizations"].append(request.headers.get("Authorization"))
        status, body = state["bank"].pop(0) if state["bank"] else (200, {"responseCode": "200"})
        return httpx.Response(status, json=body)

    real_client = httpx.Client
    monkeypatch.setattr(
        bank_client.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)
    )
    yield state
    bank_auth.invalidate()


def call(msisdn="331000001") -> CallSent:
    with SessionLocal() as db:
        return bank_client.send_mini_statement(db, MINI_STATEMENT_URL, msisdn)


def test_token_sent_as_bearer_and_reused(client, bank):
    first, second = call(), call()
    assert bank["authorizations"] == ["Bearer tok1", "Bearer tok1"]  # un seul token pour les deux appels
    assert bank["token_requests"] == [{
        "grant_type": ["client_credentials"], "client_id": ["airtel-am-loan"],
        "client_secret": ["secret"], "scope": ["pamf.read"],
    }]
    assert (first.response_status, second.response_status) == (200, 200)
    with SessionLocal() as db:
        logged = db.scalars(select(CallSent.request_headers)).all()
    assert {h["Authorization"] for h in logged} == {"Bearer ***"}  # token jamais journalisé
    assert "tok1" not in json.dumps(logged)


def test_new_token_after_expiry(client, bank, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(bank_auth.time, "monotonic", lambda: now[0])
    call()
    now[0] += 300 - bank_auth.EXPIRY_MARGIN_SECONDS - 1  # encore valable
    call()
    now[0] += 2  # dans la marge d'expiration
    call()
    assert bank["authorizations"] == ["Bearer tok1", "Bearer tok1", "Bearer tok2"]


def test_401_retried_once_with_new_token(client, bank):
    bank["bank"] = [(401, {"error": "invalid_token"})]
    assert call().response_status == 200
    assert bank["authorizations"] == ["Bearer tok1", "Bearer tok2"]

    bank["bank"] = [(401, {}), (401, {})]
    assert call().response_status == 401  # pas de nouvelle tentative au-delà
    assert len(bank["authorizations"]) == 4


@pytest.mark.parametrize(
    "token_response, error",
    [
        ((401, {"error": "unauthorized_client", "error_description": "Invalid client credentials"}),
         "Token request failed: HTTP 401: Invalid client credentials"),
        ((200, {"token_type": "Bearer"}), "Token request failed: HTTP 200"),
    ],
)
def test_token_failure_sends_nothing(client, bank, token_response, error):
    bank["token"] = lambda n: token_response
    logged = call()
    assert logged.error == error and logged.response_status is None
    assert bank["authorizations"] == []
    assert bank_client.response_json(logged) is None  # l'écran affiche « Bank call failed: Token request failed… »


def test_no_token_url_no_authorization(client, bank, monkeypatch):
    monkeypatch.setattr(get_settings(), "bank_token_url", "")
    call()
    assert bank["authorizations"] == [None] and bank["token_requests"] == []
