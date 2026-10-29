import json
import re
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import bank_client
from app.config import get_settings
from app.database import SessionLocal
from app.models import CallSent, Customer, WalletTransaction
from bank_mock import main as bank_main

DEFAULTER_URL = "/topup-notification/v1/update/loans-defaulter"


@pytest.fixture
def bank(monkeypatch):
    """Banque simulée : réponse de /topup-notification, requêtes reçues dans `requests`."""
    state = {"response": (200, {"responseCode": "200"}), "requests": []}

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append((request.url.path, json.loads(request.content)))
        if isinstance(state["response"], Exception):
            raise state["response"]
        status, body = state["response"]
        return httpx.Response(status, json=body)

    real_client = httpx.Client
    monkeypatch.setattr(
        bank_client.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)
    )
    return state


def set_defaulter(client, auth_headers, msisdn, status=True):
    payload = {"loan_info": {"id": "LN001", "type": "TERM"}, "beneficiary": {"msisdn": msisdn},
               "defaulter_info": {"amount": "1000", "status": status}}
    assert client.post(DEFAULTER_URL, json=payload, headers=auth_headers).status_code == 200


def deposit(client, msisdn, amount):
    return client.post("/wallet/deposit", data={"msisdn": msisdn, "amount": str(amount)})


def movements() -> list[WalletTransaction]:
    with SessionLocal() as db:
        return db.scalars(select(WalletTransaction).order_by(WalletTransaction.id)).all()


def test_deposit_of_defaulter_notifies_bank(client, auth_headers, customer, bank):
    deposit(client, customer, 900)
    set_defaulter(client, auth_headers, customer)
    page = deposit(client, customer, 100).text
    assert "top-up notification sent to the bank" in page

    [(path, payload)] = bank["requests"]
    first, second = movements()
    assert path == "/topup-notification"
    assert payload == {
        "msisdn": customer,
        "topUpAmount": "100",
        "transactionId": second.reference,
        "transactionTimestamp": payload["transactionTimestamp"],
        "availableBalanceAmount": "1000",
    }
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", payload["transactionTimestamp"])
    assert (first.topup_notification, second.topup_notification) == (None, "SENT")
    with SessionLocal() as db:
        assert db.get(CallSent, second.topup_call_id).flow == "topup_notification"


@pytest.mark.parametrize("change", [None, False])  # pas de statut, ou statut levé
def test_no_notification_when_not_defaulter(client, auth_headers, customer, bank, change):
    if change is False:
        set_defaulter(client, auth_headers, customer, status=False)
    deposit(client, customer, 100)
    assert bank["requests"] == []
    assert movements()[0].topup_notification is None


def test_withdrawal_of_defaulter_does_not_notify(client, auth_headers, customer, bank):
    set_defaulter(client, auth_headers, customer)
    deposit(client, customer, 100)
    client.post("/wallet/withdraw", data={"msisdn": customer, "amount": "50"})
    assert len(bank["requests"]) == 1


@pytest.mark.parametrize(
    "response, shown",
    [((200, {"responseCode": "500", "message": "boom"}), "HTTP 200"), (httpx.ConnectError("refused"), "refused")],
)
def test_failed_notification_keeps_deposit(client, auth_headers, customer, bank, response, shown):
    set_defaulter(client, auth_headers, customer)
    bank["response"] = response
    page = deposit(client, customer, 100).text
    assert "top-up notification failed" in page and shown in page
    [movement] = movements()
    assert movement.topup_notification == "FAILED"
    with SessionLocal() as db:
        assert db.scalar(select(Customer.balance)) == Decimal("100")
    assert ">Failed<" in client.get(f"/wallet?msisdn={customer}").text


def test_not_configured(client, auth_headers, customer, bank, monkeypatch):
    monkeypatch.setattr(get_settings(), "bank_topup_notification_path", "")
    set_defaulter(client, auth_headers, customer)
    assert "BANK_TOPUP_NOTIFICATION_PATH is not set" in deposit(client, customer, 100).text
    assert bank["requests"] == [] and movements()[0].topup_notification == "NOT_CONFIGURED"


# --- bank-mock ---------------------------------------------------------------

def test_bank_mock_acknowledges(tmp_path, monkeypatch):
    from bank_mock.config import get_settings as get_bank_settings

    monkeypatch.setattr(get_bank_settings(), "loans_file", str(tmp_path / "loans.json"))
    with TestClient(bank_main.app) as bank_mock:
        body = {"msisdn": "997739690", "topUpAmount": "100", "transactionId": "WLT1",
                "transactionTimestamp": "2026-10-29 10:00:00", "availableBalanceAmount": "1000"}
        assert bank_mock.post("/api/v1/topup-notification", json=body).json() == {"responseCode": "200"}
        assert bank_mock.post("/api/v1/topup-notification", json={}).json()["responseCode"] == "400"
