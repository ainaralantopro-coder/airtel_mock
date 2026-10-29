import re
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import Autostrike, CallReceive, Customer, LoanDefaulter, WalletTransaction

URL = "/standard/v1/loan/autostrike"
DEFAULTER_URL = "/topup-notification/v1/update/loans-defaulter"


def set_defaulter(client, auth_headers, msisdn, amount, status=True):
    payload = {"loan_info": {"id": "LN001", "type": "TERM"}, "beneficiary": {"msisdn": msisdn},
               "defaulter_info": {"amount": str(amount), "status": status}}
    assert client.post(DEFAULTER_URL, json=payload, headers=auth_headers).status_code == 200


@pytest.fixture(autouse=True)
def defaulter(client, auth_headers, customer):
    """Par défaut, le client de test est en défaut de 10 000 sur un prêt."""
    set_defaulter(client, auth_headers, customer, 10000)


def defaulted_amount(msisdn) -> Decimal:
    with SessionLocal() as db:
        return db.scalar(select(LoanDefaulter.amount).where(LoanDefaulter.msisdn == msisdn))


def body(msisdn="997739692", amount="1000", txn_id="LOANAUTOSTRIKE101", reference="1761298322") -> dict:
    return {
        "reference": reference,
        "subscriber": {"country": "ZM", "currency": "ZMW", "msisdn": msisdn},
        "transaction": {"amount": amount, "country": "ZM", "currency": "ZMW", "id": txn_id},
    }


def fund(client, msisdn, amount):
    client.post("/wallet/deposit", data={"msisdn": msisdn, "amount": str(amount)})


def balance(msisdn) -> Decimal:
    with SessionLocal() as db:
        return db.scalar(select(Customer.balance).where(Customer.msisdn == msisdn))


def count(model) -> int:
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(model))


def test_autostrike_success(client, auth_headers, customer):
    fund(client, customer, 1500)
    r = client.post(URL, json=body(), headers=auth_headers)
    assert r.status_code == 200
    data, status = r.json()["data"]["transaction"], r.json()["status"]

    assert data["id"] == "LOANAUTOSTRIKE101" and data["status"] == "TS"
    assert re.fullmatch(r"1\d{10}", data["reference_id"])
    assert re.fullmatch(r"AUTOSTRK-[A-Z0-9]{10}-LOANAUTOSTRIKE101", data["airtel_money_id"])
    assert status == {
        "response_code": "DP03620001001", "code": "200", "success": True, "result_code": "ESB000010",
        "message": f"transfer of ZMW 1000 completed successfully between the sender: {customer} "
                   f"and receiver: 570487456. Txn ID: {data['reference_id']}.",
    }

    assert balance(customer) == Decimal("500")
    with SessionLocal() as db:
        movement = db.scalar(select(WalletTransaction).where(WalletTransaction.type == "AUTOSTRIKE"))
        record = db.scalar(select(Autostrike))
    assert (movement.amount, movement.balance_after) == (Decimal("1000"), Decimal("500"))
    assert record.wallet_transaction_id == movement.id and record.reference == "1761298322"
    assert "Autostrike" in client.get(f"/wallet?msisdn={customer}").text


def test_insufficient_balance_debits_nothing(client, auth_headers, customer):
    fund(client, customer, 999)
    r = client.post(URL, json=body(), headers=auth_headers)
    assert r.status_code == 200
    data, status = r.json()["data"]["transaction"], r.json()["status"]
    assert data["status"] == "TF"
    assert (status["response_code"], status["code"], status["success"], status["result_code"]) == (
        "DP03620001002", "200", False, "ESB000001"
    )
    assert "failed" in status["message"] and "insufficient balance" in status["message"]
    assert balance(customer) == Decimal("999")
    assert count(Autostrike) == 1 and count(WalletTransaction) == 1  # le dépôt seulement


def test_retry_same_transaction_id_does_not_debit_twice(client, auth_headers, customer):
    fund(client, customer, 5000)
    first = client.post(URL, json=body(), headers=auth_headers).json()
    second = client.post(URL, json=body(reference="other-ref"), headers=auth_headers).json()
    assert second == first
    assert balance(customer) == Decimal("4000") and count(Autostrike) == 1


def test_retry_of_failed_transaction_stays_failed(client, auth_headers, customer):
    first = client.post(URL, json=body(), headers=auth_headers).json()
    fund(client, customer, 5000)
    assert client.post(URL, json=body(), headers=auth_headers).json() == first
    assert balance(customer) == Decimal("5000")


@pytest.mark.parametrize("change", [{"msisdn": "000"}, {"amount": "2000"}])
def test_transaction_id_reused_for_another_autostrike(client, auth_headers, customer, change):
    fund(client, customer, 5000)
    client.post(URL, json=body(), headers=auth_headers)
    r = client.post(URL, json=body(**change), headers=auth_headers)
    assert r.status_code == 409
    assert r.json()["data"] is None and r.json()["status"]["response_code"] == "DP03620004091"
    assert balance(customer) == Decimal("4000")


def test_unknown_subscriber(client, auth_headers):
    r = client.post(URL, json=body(msisdn="000"), headers=auth_headers)
    assert r.status_code == 404
    assert r.json() == {"data": None, "status": {
        "response_code": "DP03620004041", "code": "404", "success": False, "result_code": "ESB000001",
        "message": "Subscriber not found",
    }}


@pytest.mark.parametrize(
    "payload",
    [
        body(amount="0"), body(amount="-10"), body(amount="10.5"), body(amount="abc"), body(amount=True),
        body(amount="1e30"), body(txn_id=" "), body(reference=""), body(msisdn=""),
        {k: v for k, v in body().items() if k != "transaction"},
    ],
)
def test_invalid_body(client, auth_headers, customer, payload):
    fund(client, customer, 5000)
    r = client.post(URL, json=payload, headers=auth_headers)
    assert r.status_code == 400
    status = r.json()["status"]
    assert (status["response_code"], status["result_code"]) == ("DP03620004001", "ESB000001")
    assert balance(customer) == Decimal("5000")


def test_amount_as_number_and_without_currency(client, auth_headers, customer):
    fund(client, customer, 5000)
    payload = body(amount=1000)
    del payload["subscriber"]["currency"], payload["transaction"]["currency"]
    r = client.post(URL, json=payload, headers=auth_headers)
    assert r.json()["status"]["message"].startswith("transfer of 1000 completed successfully")



def test_requires_token_and_is_logged(client, auth_headers, customer):
    r = client.post(URL, json=body())
    assert r.status_code == 401 and r.json()["status"]["response_code"] == "DP03620004011"
    client.post(URL, json=body(), headers=auth_headers)
    with SessionLocal() as db:
        assert db.scalars(select(CallReceive.path)).all().count(URL) == 2


# --- Contrôles defaulter ----------------------------------------------------

def assert_failed(response, reason):
    data, status = response.json()["data"]["transaction"], response.json()["status"]
    assert response.status_code == 200 and data["status"] == "TF" and status["success"] is False
    assert f": {reason}. Txn ID:" in status["message"]


def test_not_a_defaulter(client, auth_headers, customer):
    fund(client, customer, 5000)
    set_defaulter(client, auth_headers, customer, 0, status=False)
    assert_failed(client.post(URL, json=body(), headers=auth_headers), "subscriber is not a defaulter")
    assert balance(customer) == Decimal("5000") and count(Autostrike) == 1


def test_amount_above_defaulted_amount(client, auth_headers, customer):
    fund(client, customer, 5000)
    set_defaulter(client, auth_headers, customer, 999)
    assert_failed(client.post(URL, json=body(amount="1000"), headers=auth_headers),
                  "amount exceeds the defaulted amount of 999")
    assert balance(customer) == Decimal("5000") and defaulted_amount(customer) == Decimal("999")


def test_amount_equal_to_defaulted_amount_then_nothing_left(client, auth_headers, customer):
    fund(client, customer, 5000)
    set_defaulter(client, auth_headers, customer, 1000)
    r = client.post(URL, json=body(amount="1000"), headers=auth_headers)
    assert r.json()["data"]["transaction"]["status"] == "TS"
    assert defaulted_amount(customer) == 0 and balance(customer) == Decimal("4000")

    # Dette soldée mais statut toujours actif (levé par la banque) : plus rien à prélever
    assert_failed(client.post(URL, json=body(amount="1", txn_id="T2"), headers=auth_headers),
                  "amount exceeds the defaulted amount of 0")
    with SessionLocal() as db:
        assert db.scalar(select(LoanDefaulter.is_defaulter)) is True


def test_successive_autostrikes_reduce_defaulted_amount(client, auth_headers, customer):
    fund(client, customer, 20000)
    for i, expected in enumerate(("7000", "4000", "1000"), start=1):
        client.post(URL, json=body(amount="3000", txn_id=f"T{i}"), headers=auth_headers)
        assert defaulted_amount(customer) == Decimal(expected)
    assert_failed(client.post(URL, json=body(amount="3000", txn_id="T4"), headers=auth_headers),
                  "amount exceeds the defaulted amount of 1000")
    assert balance(customer) == Decimal("11000")


def test_insufficient_balance_keeps_defaulted_amount(client, auth_headers, customer):
    fund(client, customer, 500)
    assert_failed(client.post(URL, json=body(amount="1000"), headers=auth_headers), "insufficient balance")
    assert defaulted_amount(customer) == Decimal("10000")
