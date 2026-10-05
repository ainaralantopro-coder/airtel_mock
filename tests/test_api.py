from datetime import datetime, timedelta, timezone

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, update

from app import bank_client
from app.config import get_settings
from app.database import SessionLocal
from app.models import AccessToken, CallReceive, CallSent, Customer, MessageSent
from tests.conftest import ROOT

NOTIFY_BODY = {"customer_msisdn": "997739692", "message": {"en": "Hello", "fr": "Bonjour", "es": "Hola"}}


def test_models_match_migrations():
    command.check(Config(str(ROOT / "alembic.ini")))


# --- auth -------------------------------------------------------------------

def test_token_json(client):
    r = client.post(
        "/auth/oauth2/token",
        json={"client_id": "test-client", "client_secret": "test-secret", "grant_type": "client_credentials"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["expires_in"] == 180
    assert body["token_type"] == "bearer"
    assert len(body["access_token"]) == 32


def test_token_form_urlencoded(client):
    r = client.post(
        "/auth/oauth2/token",
        data={"client_id": "test-client", "client_secret": "test-secret", "grant_type": "client_credentials"},
    )
    assert r.status_code == 200


def test_token_bad_credentials(client):
    r = client.post(
        "/auth/oauth2/token",
        json={"client_id": "test-client", "client_secret": "nope", "grant_type": "client_credentials"},
    )
    assert r.status_code == 401
    assert r.json()["error"] == "invalid_client"


def test_token_bad_grant_type(client):
    r = client.post(
        "/auth/oauth2/token",
        json={"client_id": "test-client", "client_secret": "test-secret", "grant_type": "password"},
    )
    assert r.status_code == 400
    assert r.json()["error"] == "unsupported_grant_type"


def test_token_malformed_body(client):
    r = client.post("/auth/oauth2/token", content="not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_request"


# --- users ------------------------------------------------------------------

def test_get_user(client, auth_headers, customer):
    r = client.get(f"/standard/v2/users/{customer}", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {
        "data": {
            "first_name": "Dealer",
            "grade": "SUBS",
            "is_barred": False,
            "is_pin_set": True,
            "last_name": "Test1",
            "msisdn": "997739692",
            "dob": "1990-05-12 00:00:00.0",
            "account_status": "Y",
            "nationatility": "MG",
            "id_number": "125123455522",
            "registration": {"status": "SUBS"},
        },
        "status": {"code": "200", "message": "success", "result_code": "DP02200000001", "success": True},
    }


def test_get_user_not_found(client, auth_headers):
    r = client.get("/standard/v2/users/123456789", headers=auth_headers)
    assert r.status_code == 404
    body = r.json()
    assert body["data"] is None
    assert body["status"]["success"] is False
    assert body["status"]["code"] == "404"


def test_get_user_invalid_msisdn(client, auth_headers):
    r = client.get("/standard/v2/users/12345", headers=auth_headers)
    assert r.status_code == 400


def test_get_user_requires_token(client, customer):
    assert client.get(f"/standard/v2/users/{customer}").status_code == 401
    r = client.get(f"/standard/v2/users/{customer}", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401
    assert r.json()["status"]["message"] == "Invalid access token"
    assert r.headers["WWW-Authenticate"] == "Bearer"


def test_expired_token(client, token, customer):
    with SessionLocal() as db:
        db.execute(
            update(AccessToken)
            .where(AccessToken.token == token)
            .values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        )
        db.commit()
    r = client.get(f"/standard/v2/users/{customer}", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401
    assert r.json()["status"]["message"] == "Access token expired"


# --- notify -----------------------------------------------------------------

def test_notify(client, auth_headers, customer):
    r = client.post("/term-loans/v1/notify", json=NOTIFY_BODY, headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {
        "data": {"message": "Notification sent successfully."},
        "status": {"response_code": "DP02800001001", "code": "200", "success": True, "message": "Success"},
    }
    with SessionLocal() as db:
        message = db.scalar(select(MessageSent))
    assert (message.msisdn, message.message_en, message.message_fr, message.message_es) == (
        "997739692", "Hello", "Bonjour", "Hola"
    )


def test_notify_unknown_customer(client, auth_headers):
    r = client.post("/term-loans/v1/notify", json=NOTIFY_BODY, headers=auth_headers)
    assert r.status_code == 404
    assert r.json()["status"]["success"] is False
    assert "response_code" in r.json()["status"]


def test_notify_validation(client, auth_headers, customer):
    r = client.post(
        "/term-loans/v1/notify", json={"customer_msisdn": customer, "message": {}}, headers=auth_headers
    )
    assert r.status_code == 400
    assert "at least one of en, fr, es" in r.json()["status"]["message"]

    r = client.post("/term-loans/v1/notify", json={"message": {"en": "x"}}, headers=auth_headers)
    assert r.status_code == 400


def test_notify_requires_token(client, customer):
    assert client.post("/term-loans/v1/notify", json=NOTIFY_BODY).status_code == 401


def test_incoming_calls_are_logged(client, auth_headers, customer):
    client.post("/term-loans/v1/notify", json=NOTIFY_BODY, headers=auth_headers)
    with SessionLocal() as db:
        calls = db.scalars(select(CallReceive).order_by(CallReceive.id)).all()
    assert [c.path for c in calls] == ["/auth/oauth2/token", "/term-loans/v1/notify"]
    assert calls[1].response_status == 200
    assert "Bonjour" in calls[1].request_body
    assert "Notification sent successfully." in calls[1].response_body


# --- écrans -----------------------------------------------------------------

def test_screens_render(client, customer):
    for path in ("/", "/customers/new", "/register", f"/messages?msisdn={customer}"):
        assert client.get(path).status_code == 200, path


def test_customer_form_rejects_duplicate_and_invalid(client, customer):
    r = client.post("/customers/new", data={"msisdn": customer, "first_name": "A", "last_name": "B",
                                            "dob": "1990-01-01", "id_number": "1"})
    assert r.status_code == 400
    assert "existe déjà" in r.text

    r = client.post("/customers/new", data={"msisdn": "12", "first_name": "", "dob": "x"})
    assert r.status_code == 400
    assert "9 chiffres" in r.text


OPT_IN_OK = {"responseCode": "200", "message": "User opted in successfully", "msisdn": "997739692"}


def mock_bank(monkeypatch, handler):
    real_client = httpx.Client
    monkeypatch.setattr(
        bank_client.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)
    )


def is_opted_in(msisdn: str) -> bool:
    with SessionLocal() as db:
        return db.scalar(select(Customer.opted_in).where(Customer.msisdn == msisdn))


def test_register_opt_in_success(client, customer, monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.content
        return httpx.Response(200, json=OPT_IN_OK)

    mock_bank(monkeypatch, handler)
    r = client.post("/register", data={"msisdn": customer})
    assert r.status_code == 200
    assert "opted-in." in r.text
    assert captured == {"url": "http://bank.test/opt-in", "body": b'{"msisdn": "997739692"}'}
    assert is_opted_in(customer) is True
    with SessionLocal() as db:
        call = db.scalar(select(CallSent))
    assert (call.flow, call.response_status) == ("opt_in", 200)


@pytest.mark.parametrize(
    "body",
    [
        {"responseCode": "200"},
        {"responseCode": 200},
        {"responseCode": "200", "message": "User opted in sucessfully", "msisdn": "111111111"},
    ],
)
def test_register_only_response_code_matters(client, customer, monkeypatch, body):
    mock_bank(monkeypatch, lambda request: httpx.Response(200, json=body))
    client.post("/register", data={"msisdn": customer})
    assert is_opted_in(customer) is True


@pytest.mark.parametrize(
    "status, body",
    [
        (200, {**OPT_IN_OK, "responseCode": "400"}),
        (200, {"message": "User opted in successfully", "msisdn": "997739692"}),
        (400, {"responseCode": "400", "message": "Bad request"}),
        (200, "not json"),
        (200, ["responseCode", "200"]),
    ],
)
def test_register_invalid_bank_response(client, customer, monkeypatch, status, body):
    def handler(request):
        if isinstance(body, str):
            return httpx.Response(status, text=body)
        return httpx.Response(status, json=body)

    mock_bank(monkeypatch, handler)
    r = client.post("/register", data={"msisdn": customer})
    assert "Client non opted-in" in r.text
    assert is_opted_in(customer) is False


def test_register_bank_unreachable(client, customer, monkeypatch):
    def handler(request):
        raise httpx.ConnectError("connection refused")

    mock_bank(monkeypatch, handler)
    r = client.post("/register", data={"msisdn": customer})
    assert "chec de l" in r.text
    assert is_opted_in(customer) is False
    with SessionLocal() as db:
        assert db.scalar(select(CallSent.error)).startswith("ConnectError")


def test_register_without_bank_url(client, customer, monkeypatch):
    monkeypatch.setattr(get_settings(), "bank_opt_in_path", "")
    assert "disabled" in client.get("/register").text

    r = client.post("/register", data={"msisdn": customer})
    assert r.status_code == 503
    assert is_opted_in(customer) is False
    with SessionLocal() as db:
        assert db.scalar(select(CallSent)) is None


def test_register_unknown_customer(client):
    r = client.post("/register", data={"msisdn": "111111111"})
    assert r.status_code == 404
