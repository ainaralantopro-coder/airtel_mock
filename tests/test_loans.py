import json
import re
from datetime import datetime
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from app import bank_client
from app.config import get_settings
from app.database import SessionLocal
from app.models import CallSent, Loan
from app.web import format_ariary

MSISDN = "997739692"

ELIGIBLE = {
    "responseCode": "200",
    "message": "Check Eligibility performed sucessfully",
    "data": {
        "msisdn": MSISDN,
        "minAmount": "5000",
        "maxAmount": "100000",
        "eligibleAmount": "50000",
        "feesAmount": "500",
    },
}

BOOKED = {
    "responseCode": "200",
    "message": "Loan booked sucessfully",
    "data": {
        "transactionId": "x",
        "loanId": "LN0001",
        "loanAmount": 20000,
        "loanfees": "400",
        "outstandingAmount": "20400",
        "dueDate": "2026-11-04 23:59:59",
        "tenureId": "30",
        "tenureName": "30 days",
        "interestRate": "2",
    },
}

OFFER_FORM = {"msisdn": MSISDN, "min_amount": "5000", "max_amount": "100000",
              "eligible_amount": "50000", "fees_amount": "500"}


@pytest.fixture
def bank(monkeypatch):
    """Banque simulée : réponses par chemin, requêtes reçues dans `requests`."""
    state = {"responses": {}, "requests": []}

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append((request.url.path, json.loads(request.content)))
        response = state["responses"][request.url.path]
        if isinstance(response, Exception):
            raise response
        status, body = response
        return httpx.Response(status, json=body) if not isinstance(body, str) else httpx.Response(status, text=body)

    real_client = httpx.Client
    monkeypatch.setattr(
        bank_client.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)
    )
    return state


def loans() -> list[Loan]:
    with SessionLocal() as db:
        return db.scalars(select(Loan).order_by(Loan.id)).all()


# --- Check Eligibility ------------------------------------------------------

def test_eligibility_shows_offer(client, bank):
    bank["responses"]["/eligibility"] = (200, ELIGIBLE)
    r = client.post("/loans/borrow/eligibility", data={"msisdn": MSISDN})
    assert r.status_code == 200
    assert bank["requests"] == [("/eligibility", {"msisdn": MSISDN, "loanAmount": None})]
    assert "Borrow up to <strong>Ar 100 000</strong>" in r.text
    assert "Eligible amount: <strong>Ar 50 000</strong>" in r.text
    assert 'name="amount" value="5000"' in r.text
    with SessionLocal() as db:
        assert db.scalar(select(CallSent.flow)) == "check_eligibility"


def test_eligibility_works_without_customer_or_opt_in(client, bank):
    bank["responses"]["/eligibility"] = (200, ELIGIBLE)
    r = client.post("/loans/borrow/eligibility", data={"msisdn": "123456789"})
    assert 'name="amount"' in r.text


@pytest.mark.parametrize(
    "response, expected",
    [
        ((200, {"responseCode": "400", "message": "Customer not eligible"}), "Customer not eligible"),
        ((500, "boom"), "Réponse illisible"),
        (httpx.ConnectError("refused"), "chec de l&#39;appel"),
        ((200, {"responseCode": "200", "data": {"minAmount": "", "eligibleAmount": "1"}}), "incomplète"),
        ((200, {"responseCode": "200", "data": {"minAmount": "5000", "eligibleAmount": "1000"}}), "aucun prêt"),
    ],
)
def test_eligibility_failure_hides_amount_field(client, bank, response, expected):
    bank["responses"]["/eligibility"] = response
    r = client.post("/loans/borrow/eligibility", data={"msisdn": MSISDN})
    assert expected in r.text
    assert 'name="amount"' not in r.text


def test_borrow_disabled_without_bank_paths(client, bank, monkeypatch):
    monkeypatch.setattr(get_settings(), "bank_apply_loan_path", "")
    assert "disabled" in client.get("/loans/borrow").text
    r = client.post("/loans/borrow/eligibility", data={"msisdn": MSISDN})
    assert r.status_code == 503
    assert bank["requests"] == []


# --- Apply Loan -------------------------------------------------------------

def test_apply_loan_booked(client, bank):
    bank["responses"]["/apply-loan"] = (200, BOOKED)
    r = client.post("/loans/borrow/apply", data={**OFFER_FORM, "amount": "20000"})
    assert r.status_code == 200
    assert "Loan booked sucessfully" in r.text

    path, payload = bank["requests"][0]
    assert path == "/apply-loan"
    assert payload["loanAmount"] == 20000 and payload["msisdn"] == MSISDN
    assert payload["transactionId"].startswith("APC") and "tenureId" not in payload

    [loan] = loans()
    assert loan.status == "BOOKED"
    assert loan.transaction_id == payload["transactionId"]
    assert (loan.requested_amount, loan.fees_amount) == (20000, Decimal("500"))
    assert (loan.loan_id, loan.loan_amount, loan.loan_fees, loan.outstanding_amount) == (
        "LN0001", Decimal("20000"), Decimal("400"), Decimal("20400")
    )
    assert loan.due_date == datetime(2026, 11, 4, 23, 59, 59)
    assert (loan.tenure_id, loan.tenure_name, loan.interest_rate) == ("30", "30 days", "2")
    assert loan.is_disbursed is False


def test_apply_loan_failed_is_saved(client, bank):
    bank["responses"]["/apply-loan"] = (200, {"responseCode": "409", "message": "Customer has an active loan"})
    r = client.post("/loans/borrow/apply", data={**OFFER_FORM, "amount": "20000"})
    assert "Customer has an active loan" in r.text
    [loan] = loans()
    assert (loan.status, loan.response_code, loan.response_message) == (
        "FAILED", "409", "Customer has an active loan"
    )


def test_apply_loan_bank_unreachable_is_saved(client, bank):
    bank["responses"]["/apply-loan"] = httpx.ConnectError("refused")
    client.post("/loans/borrow/apply", data={**OFFER_FORM, "amount": "20000"})
    [loan] = loans()
    assert loan.status == "FAILED"
    assert loan.response_message.startswith("Échec de l'appel")


@pytest.mark.parametrize("amount", ["4999", "50001", "abc", "10000.5", ""])
def test_apply_loan_amount_out_of_range(client, bank, amount):
    r = client.post("/loans/borrow/apply", data={**OFFER_FORM, "amount": amount})
    assert r.status_code == 400
    assert "entre 5000 et 50000" in r.text
    assert bank["requests"] == [] and loans() == []


@pytest.mark.parametrize("amount", ["5000", "50000"])
def test_apply_loan_amount_bounds_included(client, bank, amount):
    bank["responses"]["/apply-loan"] = (200, BOOKED)
    client.post("/loans/borrow/apply", data={**OFFER_FORM, "amount": amount})
    assert bank["requests"][0][1]["loanAmount"] == int(amount)


def test_transaction_id_format():
    ids = {bank_client.new_transaction_id() for _ in range(50)}
    assert len(ids) == 50
    assert all(re.fullmatch(r"APC\d{22}", i) for i in ids)


# --- Liste ------------------------------------------------------------------

def test_loan_list_filters_by_msisdn(client, bank):
    bank["responses"]["/apply-loan"] = (200, BOOKED)
    client.post("/loans/borrow/apply", data={**OFFER_FORM, "amount": "20000"})
    client.post("/loans/borrow/apply", data={**OFFER_FORM, "msisdn": "111111111", "amount": "6000"})

    r = client.get(f"/loans?msisdn={MSISDN}")
    assert "LN0001" in r.text and "Ar 20 000" in r.text
    assert "Ar 6 000" not in r.text
    assert "Aucun prêt" in client.get("/loans?msisdn=222222222").text
    assert client.get("/loans").status_code == 200


def test_format_ariary():
    assert format_ariary(50000) == "Ar 50 000"
    assert format_ariary(Decimal("1234.50")) == "Ar 1 234.50"
    assert format_ariary(None) == ""
