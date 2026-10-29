from decimal import Decimal

import pytest
from sqlalchemy import select

from app.database import SessionLocal
from app.models import CallReceive, Loan, LoanDefaulter

URL = "/topup-notification/v1/update/loans-defaulter"
SUCCESS = {"status": {"response_code": "DP02810001001", "code": "200", "success": True, "message": "Success"}}


def body(msisdn="997739692", loan_id="LN001", loan_type="OVERDRAFT", amount="400", status=True) -> dict:
    loan_info = {"type": loan_type} if loan_id is None else {"id": loan_id, "type": loan_type}
    return {"loan_info": loan_info, "beneficiary": {"msisdn": msisdn},
            "defaulter_info": {"amount": amount, "status": status}}


def defaulters() -> list[LoanDefaulter]:
    with SessionLocal() as db:
        return db.scalars(select(LoanDefaulter).order_by(LoanDefaulter.id)).all()


def test_set_defaulter_status(client, auth_headers, customer):
    r = client.post(URL, json=body(), headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == SUCCESS

    [d] = defaulters()
    assert (d.msisdn, d.loan_ref, d.loan_type, d.amount, d.is_defaulter, d.loan_pk) == (
        customer, "LN001", "OVERDRAFT", Decimal("400"), True, None
    )


def test_same_loan_is_updated_not_duplicated(client, auth_headers, customer):
    client.post(URL, json=body(), headers=auth_headers)
    client.post(URL, json=body(amount=0, status=False), headers=auth_headers)
    [d] = defaulters()
    assert (d.amount, d.is_defaulter) == (Decimal("0"), False)

    client.post(URL, json=body(loan_type="TERM"), headers=auth_headers)  # même id, autre type
    client.post(URL, json=body(loan_id=None), headers=auth_headers)  # sans id
    client.post(URL, json=body(loan_id=None, amount="150.50"), headers=auth_headers)
    assert [(d.loan_type, d.loan_ref, d.amount) for d in defaulters()] == [
        ("OVERDRAFT", "LN001", Decimal("0")),
        ("TERM", "LN001", Decimal("400")),
        ("OVERDRAFT", None, Decimal("150.50")),
    ]


@pytest.mark.parametrize("loan_info", [{"type": "TERM"}, {"id": None, "type": "TERM"}, {"id": " ", "type": "TERM"}])
def test_loan_id_is_optional(client, auth_headers, customer, loan_info):
    payload = {**body(), "loan_info": loan_info}
    assert client.post(URL, json=payload, headers=auth_headers).json() == SUCCESS
    [d] = defaulters()
    assert d.loan_ref is None


def test_term_loan_linked_to_mock_loan(client, auth_headers, customer):
    with SessionLocal() as db:
        db.add(Loan(msisdn=customer, transaction_id="APC1", requested_amount=20000, status="BOOKED",
                    loan_id="00000010604183833", is_disbursed=True))
        db.commit()
    client.post(URL, json=body(loan_id="00000010604183833", loan_type="TERM"), headers=auth_headers)
    [d] = defaulters()
    assert d.loan_pk is not None

    page = client.get("/defaulters").text
    assert "APC1" in page and "00000010604183833" in page and ">Yes<" in page


def test_unknown_customer(client, auth_headers):
    r = client.post(URL, json=body(msisdn="000000000"), headers=auth_headers)
    assert r.status_code == 404
    assert r.json() == {"status": {"response_code": "DP02810004041", "code": "404", "success": False,
                                   "message": "Customer not found"}}
    assert defaulters() == []


@pytest.mark.parametrize(
    "change",
    [
        {"loan_info": {"id": "LN001", "type": "overdraft"}},
        {"loan_info": {"id": "LN001", "type": "OTHER"}},
        {"loan_info": {"id": "LN001"}},
        {"defaulter_info": {"amount": "400", "status": "true"}},
        {"defaulter_info": {"amount": "400"}},
        {"defaulter_info": {"amount": "-1", "status": True}},
        {"defaulter_info": {"amount": "abc", "status": True}},
        {"defaulter_info": {"status": True}},
        {"beneficiary": {"msisdn": ""}},
        {"beneficiary": {}},
    ],
)
def test_invalid_body(client, auth_headers, customer, change):
    r = client.post(URL, json={**body(), **change}, headers=auth_headers)
    assert r.status_code == 400
    status = r.json()["status"]
    assert set(r.json()) == {"status"}  # pas de "data"
    assert (status["response_code"], status["success"]) == ("DP02810004001", False)
    assert status["message"].startswith("Invalid request:")


def test_requires_token_and_is_logged(client, auth_headers, customer):
    r = client.post(URL, json=body())
    assert r.status_code == 401
    assert r.json()["status"]["response_code"] == "DP02810004011"

    client.post(URL, json=body(), headers=auth_headers)
    with SessionLocal() as db:
        paths = db.scalars(select(CallReceive.path)).all()
    assert paths.count(URL) == 2


def test_defaulters_page_filter(client, auth_headers, customer):
    assert "No defaulter status received yet." in client.get("/defaulters").text
    client.post(URL, json=body(), headers=auth_headers)
    assert "LN001" in client.get(f"/defaulters?msisdn={customer}").text
    assert "No defaulter status for 123" in client.get("/defaulters?msisdn=123").text
