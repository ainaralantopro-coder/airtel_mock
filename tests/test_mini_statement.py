import json

import httpx
import pytest
from markupsafe import escape
from sqlalchemy import select

from app import bank_client
from app.config import get_settings
from app.database import SessionLocal
from app.models import CallSent

STATEMENT = {
    "responseCode": "200",
    "message": "User Mini Statement fetched sucessfully",
    "transactionDetails": [
        {"txnType": "DISBURSEMENT", "amount": 2018.0, "currency": "MWK", "txnDateTime": "2022-04-12T12:19:09.287",
         "message": "", "addDetails": {"loanId": "2018"}},
    ],
}


@pytest.fixture
def bank(monkeypatch):
    state = {"response": (200, STATEMENT), "requests": []}

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


def test_shows_response_json(client, bank):
    r = client.post("/mini-statement", data={"msisdn": "997739692"})
    assert r.status_code == 200
    assert bank["requests"] == [("/mini-statement", {"msisdn": "997739692"})]
    assert str(escape(json.dumps(STATEMENT, indent=2))) in r.text
    with SessionLocal() as db:
        assert db.scalar(select(CallSent.flow)) == "mini_statement"


def test_other_response_code_shown_as_is(client, bank):
    bank["response"] = (200, {"responseCode": "404", "message": "No transaction"})
    r = client.post("/mini-statement", data={"msisdn": "1"})
    assert "No transaction" in r.text and "alert error" not in r.text


@pytest.mark.parametrize(
    "response, error, raw",
    [
        (httpx.ConnectError("refused"), "Bank call failed: ConnectError: refused", None),
        ((500, "boom"), "Unreadable bank response (HTTP 500): not JSON.", "boom"),
    ],
)
def test_technical_errors(client, bank, response, error, raw):
    bank["response"] = response
    r = client.post("/mini-statement", data={"msisdn": "1"})
    assert error in r.text
    assert ("Raw response" in r.text) == (raw is not None)


def test_msisdn_required_and_path_missing(client, bank, monkeypatch):
    assert client.post("/mini-statement", data={"msisdn": " "}).status_code == 400
    monkeypatch.setattr(get_settings(), "bank_mini_statement_path", "")
    assert "disabled" in client.get("/mini-statement").text
    assert client.post("/mini-statement", data={"msisdn": "1"}).status_code == 503
    assert bank["requests"] == []
