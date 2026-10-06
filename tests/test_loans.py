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
        ((500, "boom"), "Unreadable bank response"),
        (httpx.ConnectError("refused"), "Bank call failed"),
        ((200, {"responseCode": "200", "data": {"minAmount": "", "eligibleAmount": "1"}}), "Incomplete bank response"),
        ((200, {"responseCode": "200", "data": {"minAmount": "5000", "eligibleAmount": "1000"}}), "no loan possible"),
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
    assert loan.response_message.startswith("Bank call failed")


@pytest.mark.parametrize("amount", ["4999", "50001", "abc", "10000.5", ""])
def test_apply_loan_amount_out_of_range(client, bank, amount):
    r = client.post("/loans/borrow/apply", data={**OFFER_FORM, "amount": amount})
    assert r.status_code == 400
    assert "between 5000 and 50000" in r.text
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
    assert "No loans" in client.get("/loans?msisdn=222222222").text
    assert client.get("/loans").status_code == 200


def test_format_ariary():
    assert format_ariary(50000) == "Ar 50 000"
    assert format_ariary(Decimal("1234.50")) == "Ar 1 234.50"
    assert format_ariary(None) == ""


# --- Disburse (Confirm Loan) -------------------------------------------------

def book_loan(client, bank) -> Loan:
    bank["responses"]["/apply-loan"] = (200, BOOKED)
    client.post("/loans/borrow/apply", data={**OFFER_FORM, "amount": "20000"})
    [loan] = loans()
    return loan


def test_disburse_button_only_on_booked_loans(client, bank):
    book_loan(client, bank)
    bank["responses"]["/apply-loan"] = (200, {"responseCode": "409", "message": "Customer has an active loan"})
    client.post("/loans/borrow/apply", data={**OFFER_FORM, "amount": "20000"})
    page = client.get(f"/loans?msisdn={MSISDN}").text
    assert page.count("Disburse OK") == 1 and page.count("Disburse KO") == 1


def test_disburse_confirms_loan(client, bank):
    loan = book_loan(client, bank)
    bank["responses"]["/confirm-loan"] = (200, {"responseCode": "200", "message": "Loan confirmed sucessfully"})
    r = client.post(f"/loans/{loan.id}/disburse")
    assert r.status_code == 200
    assert "Loan confirmed sucessfully" in r.text and "Disburse OK" not in r.text

    path, payload = bank["requests"][-1]
    assert path == "/confirm-loan"
    assert {k: payload[k] for k in ("msisdn", "transactionId", "loanAmount", "loanId")} == {
        "msisdn": MSISDN, "transactionId": loan.transaction_id, "loanAmount": 20000, "loanId": "LN0001"
    }
    assert re.fullmatch(r"APC\d{22}", payload["externalTransactionId"])

    [loan] = loans()
    assert loan.is_disbursed is True and loan.disbursed_at is not None
    assert loan.external_transaction_id == payload["externalTransactionId"]

    r = client.post(f"/loans/{loan.id}/disburse")
    assert r.status_code == 400 and "already disbursed" in r.text
    assert len(bank["requests"]) == 2


def test_disburse_refused_by_bank(client, bank):
    loan = book_loan(client, bank)
    bank["responses"]["/confirm-loan"] = (200, {"responseCode": "404", "message": "Loan not found"})
    r = client.post(f"/loans/{loan.id}/disburse")
    assert "Loan not found" in r.text and "Disburse OK" in r.text
    [loan] = loans()
    assert loan.is_disbursed is False and loan.external_transaction_id is None


def test_disburse_failed_loan_rejected(client, bank):
    bank["responses"]["/apply-loan"] = (200, {"responseCode": "409", "message": "Customer has an active loan"})
    client.post("/loans/borrow/apply", data={**OFFER_FORM, "amount": "20000"})
    [loan] = loans()
    r = client.post(f"/loans/{loan.id}/disburse")
    assert r.status_code == 400 and "Only a booked loan" in r.text
    assert client.post(f"/loans/{loan.id}/cancel").status_code == 400
    assert client.post("/loans/999999/disburse").status_code == 404


def test_disburse_disabled_without_bank_path(client, bank, monkeypatch):
    loan = book_loan(client, bank)
    monkeypatch.setattr(get_settings(), "bank_confirm_loan_path", "")
    assert "BANK_CONFIRM_LOAN_PATH is not set" in client.get(f"/loans?msisdn={MSISDN}").text
    assert client.post(f"/loans/{loan.id}/disburse").status_code == 503
    assert len(bank["requests"]) == 1


# --- Disburse KO (Cancel Loan) ----------------------------------------------

CANCELLED = {"responseCode": "200", "message": "Loan Cancelled sucessfully", "msisdn": MSISDN}


def test_cancel_loan(client, bank):
    loan = book_loan(client, bank)
    bank["responses"]["/cancel-loan"] = (200, CANCELLED)
    r = client.post(f"/loans/{loan.id}/cancel")
    assert r.status_code == 200
    assert "Loan Cancelled sucessfully" in r.text and "CANCELLED" in r.text
    assert "Disburse OK" not in r.text and "Disburse KO" not in r.text

    assert bank["requests"][-1] == ("/cancel-loan", {"msisdn": MSISDN, "transactionId": loan.transaction_id})
    [loan] = loans()
    assert loan.status == "CANCELLED" and loan.cancelled_at is not None and loan.is_disbursed is False

    assert client.post(f"/loans/{loan.id}/cancel").status_code == 400
    assert client.post(f"/loans/{loan.id}/disburse").status_code == 400
    assert len(bank["requests"]) == 2


def test_cancel_refused_by_bank(client, bank):
    loan = book_loan(client, bank)
    bank["responses"]["/cancel-loan"] = (200, {"responseCode": "409", "message": "Loan already confirmed"})
    r = client.post(f"/loans/{loan.id}/cancel")
    assert "Loan already confirmed" in r.text and "Disburse KO" in r.text
    [loan] = loans()
    assert loan.status == "BOOKED" and loan.cancelled_at is None


def test_cancel_after_disburse_rejected(client, bank):
    loan = book_loan(client, bank)
    bank["responses"]["/confirm-loan"] = (200, {"responseCode": "200", "message": "Loan confirmed sucessfully"})
    client.post(f"/loans/{loan.id}/disburse")
    r = client.post(f"/loans/{loan.id}/cancel")
    assert r.status_code == 400 and "already disbursed" in r.text
    assert [path for path, _ in bank["requests"]] == ["/apply-loan", "/confirm-loan"]


def test_cancel_disabled_without_bank_path(client, bank, monkeypatch):
    loan = book_loan(client, bank)
    monkeypatch.setattr(get_settings(), "bank_cancel_loan_path", "")
    assert "BANK_CANCEL_LOAN_PATH is not set" in client.get(f"/loans?msisdn={MSISDN}").text
    assert client.post(f"/loans/{loan.id}/cancel").status_code == 503


# --- Timeout (Apply Loan Status) ---------------------------------------------

def status_of(loan_pk: int, client) -> str:
    return client.post(f"/loans/{loan_pk}/status").text


def test_timeout_button_visibility(client, bank):
    book_loan(client, bank)
    bank["responses"]["/apply-loan"] = (200, {"responseCode": "409", "message": "Customer has an active loan"})
    client.post("/loans/borrow/apply", data={**OFFER_FORM, "amount": "20000"})
    bank["responses"]["/apply-loan"] = httpx.ReadTimeout("timed out")
    client.post("/loans/borrow/apply", data={**OFFER_FORM, "amount": "20000"})
    # BOOKED en attente + FAILED sans réponse : oui ; FAILED avec responseCode : non
    assert client.get(f"/loans?msisdn={MSISDN}").text.count(">Timeout</button>") == 2


def test_timeout_status_booked_recovers_failed_loan(client, bank):
    bank["responses"]["/apply-loan"] = httpx.ReadTimeout("timed out")
    client.post("/loans/borrow/apply", data={**OFFER_FORM, "amount": "20000"})
    [loan] = loans()
    assert (loan.status, loan.response_code, loan.loan_id) == ("FAILED", None, None)

    bank["responses"]["/apply-loan-status"] = (200, BOOKED)
    page = status_of(loan.id, client)
    assert "Apply Loan Status for" in page and "Disburse OK" in page

    assert bank["requests"][-1] == ("/apply-loan-status", {"msisdn": MSISDN, "transactionId": loan.transaction_id})
    [loan] = loans()
    assert (loan.status, loan.response_code, loan.loan_id) == ("BOOKED", "200", "LN0001")
    assert (loan.loan_amount, loan.outstanding_amount) == (Decimal("20000"), Decimal("20400"))


def test_timeout_status_pending_then_failed(client, bank):
    loan = book_loan(client, bank)
    bank["responses"]["/apply-loan-status"] = (200, {"responseCode": "202", "message": "Loan request in progress"})
    page = status_of(loan.id, client)
    assert "alert warning" in page and "Loan request in progress" in page
    assert "Disburse OK" not in page and ">Timeout</button>" in page
    [loan] = loans()
    assert loan.status == "PENDING" and loan.loan_id == "LN0001"

    bank["responses"]["/apply-loan-status"] = (200, {"responseCode": "400", "message": "Loan request failed"})
    page = status_of(loan.id, client)
    assert "alert error" in page and ">Timeout</button>" not in page
    [loan] = loans()
    assert (loan.status, loan.response_code, loan.response_message) == ("FAILED", "400", "Loan request failed")
    assert client.post(f"/loans/{loan.id}/status").status_code == 400


def test_timeout_without_answer_leaves_loan_unchanged(client, bank):
    loan = book_loan(client, bank)
    bank["responses"]["/apply-loan-status"] = httpx.ConnectError("refused")
    page = status_of(loan.id, client)
    assert "Bank call failed" in page and "Loan unchanged." in page
    [loan] = loans()
    assert (loan.status, loan.response_code) == ("BOOKED", "200")


def test_timeout_not_allowed_after_disbursement(client, bank):
    loan = book_loan(client, bank)
    bank["responses"]["/confirm-loan"] = (200, {"responseCode": "200", "message": "Loan confirmed sucessfully"})
    client.post(f"/loans/{loan.id}/disburse")
    r = client.post(f"/loans/{loan.id}/status")
    assert r.status_code == 400 and "cannot be checked" in r.text
    assert client.post("/loans/999999/status").status_code == 404


def test_timeout_disabled_without_bank_path(client, bank, monkeypatch):
    loan = book_loan(client, bank)
    monkeypatch.setattr(get_settings(), "bank_apply_loan_status_path", "")
    assert "BANK_APPLY_LOAN_STATUS_PATH is not set" in client.get(f"/loans?msisdn={MSISDN}").text
    assert client.post(f"/loans/{loan.id}/status").status_code == 503
