import json

import httpx
import pytest
from sqlalchemy import select

from app import bank_client
from app.config import get_settings
from app.database import SessionLocal
from app.models import CallSent
from app.web import format_ariary


@pytest.fixture
def bank(monkeypatch):
    state = {"response": (200, {"responseCode": "200", "message": "User Balance fetched successfully",
                                "balance": "1000"}), "requests": []}

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append((request.url.path, json.loads(request.content)))
        if isinstance(state["response"], Exception):
            raise state["response"]
        status, body = state["response"]
        return httpx.Response(status, json=body) if not isinstance(body, str) else httpx.Response(status, text=body)

    real_client = httpx.Client
    monkeypatch.setattr(
        bank_client.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)
    )
    return state


def test_shows_balance(client, bank):
    r = client.post("/balance-enquiry", data={"msisdn": "997739692"})
    assert r.status_code == 200
    assert bank["requests"] == [("/balance-enquiry", {"msisdn": "997739692"})]
    assert format_ariary(1000) in r.text and "User Balance fetched successfully" in r.text
    with SessionLocal() as db:
        assert db.scalar(select(CallSent.flow)) == "balance_enquiry"


@pytest.mark.parametrize("balance, shown", [("1000.50", format_ariary("1000.50")), ("n/a", "n/a"), ("", "No balance returned")])
def test_balance_values(client, bank, balance, shown):
    bank["response"] = (200, {"responseCode": "200", "message": "ok", "balance": balance})
    assert shown in client.post("/balance-enquiry", data={"msisdn": "1"}).text


@pytest.mark.parametrize(
    "response, error",
    [
        ((200, {"responseCode": "404", "message": "User not found"}), "User not found"),
        ((500, "boom"), "Unreadable bank response (HTTP 500)."),
        (httpx.ConnectError("refused"), "Bank call failed: ConnectError: refused"),
    ],
)
def test_errors(client, bank, response, error):
    bank["response"] = response
    r = client.post("/balance-enquiry", data={"msisdn": "1"})
    assert error in r.text and "wallet-balance" not in r.text


def test_msisdn_required_and_path_missing(client, bank, monkeypatch):
    assert client.post("/balance-enquiry", data={"msisdn": " "}).status_code == 400
    monkeypatch.setattr(get_settings(), "bank_balance_enquiry_path", "")
    assert "disabled" in client.get("/balance-enquiry").text
    assert client.post("/balance-enquiry", data={"msisdn": "1"}).status_code == 503
    assert bank["requests"] == []
