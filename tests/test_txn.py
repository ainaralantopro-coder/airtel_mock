import pytest
from sqlalchemy import select

from app.database import SessionLocal
from app.models import CallReceive, Loan

URL = "/term-loans/v1/txn/enquiry"
TXN_ID = "APC2026102210000012345678"
LOAN_ID = "00000010604183833"


def add_loan(status="BOOKED", is_disbursed=False, loan_id=LOAN_ID, transaction_id=TXN_ID) -> None:
    with SessionLocal() as db:
        db.add(Loan(msisdn="331245678", transaction_id=transaction_id, requested_amount=20000, status=status,
                    loan_id=loan_id, is_disbursed=is_disbursed))
        db.commit()


@pytest.mark.parametrize(
    "status, is_disbursed, txn_status",
    [
        ("BOOKED", True, "PROCESSED"),
        ("BOOKED", False, "PENDING"),
        ("PENDING", False, "PENDING"),
        ("CANCELLED", False, "FAILED"),
        ("FAILED", False, "FAILED"),
    ],
)
def test_txn_enquiry(client, auth_headers, status, is_disbursed, txn_status):
    add_loan(status, is_disbursed)
    r = client.post(URL, json={"txn_id": TXN_ID, "loan_id": LOAN_ID}, headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {
        "data": {"txn_id": TXN_ID, "txn_status": txn_status, "loan_id": LOAN_ID},
        "status": {"response_code": "DP02800001001", "code": "200", "success": True, "message": "Success"},
    }


@pytest.mark.parametrize(
    "body",
    [
        {"txn_id": "APC-UNKNOWN", "loan_id": LOAN_ID},
        {"txn_id": TXN_ID, "loan_id": "999"},  # loan_id d'un autre prêt
    ],
)
def test_txn_enquiry_not_found(client, auth_headers, body):
    add_loan()
    r = client.post(URL, json=body, headers=auth_headers)
    assert r.status_code == 404
    assert r.json() == {
        "data": None,
        "status": {"response_code": "DP02800004041", "code": "404", "success": False,
                   "message": "Transaction not found"},
    }


def test_txn_enquiry_empty_loan_id_rejected(client, auth_headers):
    # Une demande échouée n'a pas de loan_id : un loan_id vide ne doit pas la retrouver
    add_loan(status="FAILED", loan_id=None)
    r = client.post(URL, json={"txn_id": TXN_ID, "loan_id": ""}, headers=auth_headers)
    assert r.status_code == 400


@pytest.mark.parametrize(
    "body", [{"txn_id": TXN_ID}, {"loan_id": LOAN_ID}, {"txn_id": " ", "loan_id": LOAN_ID}, {}]
)
def test_txn_enquiry_both_fields_required(client, auth_headers, body):
    r = client.post(URL, json=body, headers=auth_headers)
    assert r.status_code == 400
    assert r.json()["status"]["response_code"] == "DP02800004001"


def test_txn_enquiry_requires_token_and_is_logged(client, auth_headers):
    assert client.post(URL, json={"txn_id": TXN_ID, "loan_id": LOAN_ID}).status_code == 401
    add_loan()
    client.post(URL, json={"txn_id": TXN_ID, "loan_id": LOAN_ID}, headers=auth_headers)
    with SessionLocal() as db:
        paths = db.scalars(select(CallReceive.path).order_by(CallReceive.id)).all()
    assert paths.count(URL) == 2
