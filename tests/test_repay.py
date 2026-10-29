import json
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from app import bank_client
from app.config import get_settings
from app.database import SessionLocal
from app.models import Customer, Loan, LoanRepayment, WalletTransaction
from app.web import format_ariary

BORROWER = "997739692"  # fixture customer
PAYER = "331111111"

LOOKUP = {
    "responseCode": "200",
    "message": "Loan lookup Details fetched sucessfully",
    "msisdn": BORROWER,
    "data": {
        "additionalInfo": {"loanInfo": [
            {"loanId": "OLD", "totalPrincipleOutstanding": "0", "totalFeesOutstanding": "0",
             "totalAmountOutstanding": "0", "issuedDate": "2026-08-01", "dueDate": "2026-08-31"},
            {"loanId": "LN0001", "totalPrincipleIssued": "20000", "totalFeesIssued": "1800",
             "totalAmountIssued": "21800", "totalPrincipleOutstanding": "15000", "totalFeesOutstanding": "1800",
             "totalAmountOutstanding": "16800", "issuedDate": "2026-10-01", "dueDate": "2026-10-31"},
        ]},
        "totalAmount": "16800", "minAmount": "500", "maxAmount": "16800",
    },
}
REPAID = {"responseCode": "200", "message": "Loan repaid successfully",
          "data": {"transactionId": "x", "loanId": "LN0001", "totalAmtPaid": "5000", "totalOutstandingAmt": "11800"}}
OFFER_FORM = {"msisdn": BORROWER, "loan_id": "LN0001", "principle_outstanding": "15000", "fees_outstanding": "1800",
              "amount_outstanding": "16800", "min_amount": "500", "max_amount": "16800", "total_amount": "16800",
              "issued_date": "2026-10-01", "due_date": "2026-10-31"}


@pytest.fixture
def bank(monkeypatch):
    """Banque simulée : réponses par chemin, requêtes reçues dans `requests`."""
    state = {"responses": {"/lookup-details": (200, LOOKUP), "/repay-loan": (200, REPAID)}, "requests": []}

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


@pytest.fixture
def payer(client):
    client.post("/customers/new", data={"msisdn": PAYER, "first_name": "Pay", "last_name": "Er",
                                        "dob": "1990-01-01", "id_number": "2"})
    client.post("/wallet/deposit", data={"msisdn": PAYER, "amount": "10000"})
    return PAYER


def balance(msisdn) -> Decimal:
    with SessionLocal() as db:
        return db.scalar(select(Customer.balance).where(Customer.msisdn == msisdn))


def repayments() -> list[LoanRepayment]:
    with SessionLocal() as db:
        return db.scalars(select(LoanRepayment).order_by(LoanRepayment.id)).all()


def pay(client, payer_msisdn=PAYER, amount="5000", **overrides):
    return client.post("/repay/pay", data={**OFFER_FORM, "payer_msisdn": payer_msisdn, "amount": amount, **overrides})


# --- Lookup -------------------------------------------------------------------

def test_lookup_shows_outstanding_principle_of_active_loan(client, customer, bank):
    r = client.post("/repay/lookup", data={"msisdn": BORROWER})
    assert r.status_code == 200
    assert f"Current outstanding amount is <strong>{format_ariary(15000)}</strong>" in r.text
    assert "LN0001" in r.text and format_ariary(16800) in r.text
    assert 'name="payer_msisdn" value="997739692"' in r.text  # pay for self par défaut
    assert bank["requests"] == [("/lookup-details", {"msisdn": BORROWER})]


@pytest.mark.parametrize(
    "response, expected",
    [
        ((200, {"responseCode": "404", "message": "No loan found"}), "No loan found"),
        ((200, {"responseCode": "200", "data": {"additionalInfo": {"loanInfo": []}}}), "no loan"),
        ((500, "boom"), "Unreadable bank response"),
        (httpx.ConnectError("refused"), "Bank call failed"),
    ],
)
def test_lookup_failures_hide_repay_form(client, bank, response, expected):
    bank["responses"]["/lookup-details"] = response
    r = client.post("/repay/lookup", data={"msisdn": BORROWER})
    assert expected in r.text and 'name="payer_msisdn"' not in r.text


def test_lookup_without_outstanding(client, bank):
    only_old = {**LOOKUP, "data": {**LOOKUP["data"], "additionalInfo": {"loanInfo": [
        LOOKUP["data"]["additionalInfo"]["loanInfo"][0]]}}}
    bank["responses"]["/lookup-details"] = (200, only_old)
    r = client.post("/repay/lookup", data={"msisdn": BORROWER})
    assert "no outstanding amount" in r.text and 'name="payer_msisdn"' not in r.text


def test_lookup_disabled_without_path(client, bank, monkeypatch):
    monkeypatch.setattr(get_settings(), "bank_lookup_details_path", "")
    assert "disabled" in client.get("/repay").text
    assert client.post("/repay/lookup", data={"msisdn": BORROWER}).status_code == 503
    assert bank["requests"] == []


# --- Repay ----------------------------------------------------------------------

def test_pay_for_others(client, customer, payer, bank):
    with SessionLocal() as db:
        db.add(Loan(msisdn=BORROWER, transaction_id="APC1", requested_amount=20000, status="BOOKED",
                    loan_id="LN0001", outstanding_amount=Decimal("21800"), is_disbursed=True))
        db.commit()

    r = client.post(
        "/repay/pay", data={**OFFER_FORM, "payer_msisdn": PAYER, "amount": "5000"}, follow_redirects=False
    )
    assert r.status_code == 303 and r.headers["location"].startswith(f"/repay?msisdn={BORROWER}&done=APC")

    path, payload = bank["requests"][-1]
    assert path == "/repay-loan"
    assert payload == {"repaymentAmount": "5000", "msisdn": BORROWER, "transactionId": payload["transactionId"],
                       "loanId": "LN0001"}

    [repayment] = repayments()
    assert (repayment.status, repayment.payer_msisdn, repayment.borrower_msisdn) == ("SUCCESS", PAYER, BORROWER)
    assert (repayment.total_amt_paid, repayment.total_outstanding_amt) == (Decimal("5000"), Decimal("11800"))
    assert balance(PAYER) == Decimal("5000") and balance(BORROWER) == 0

    with SessionLocal() as db:
        movement = db.get(WalletTransaction, repayment.wallet_transaction_id)
        assert db.scalar(select(Loan.outstanding_amount)) == Decimal("11800")
    assert (movement.type, movement.msisdn, movement.amount) == ("REPAYMENT", PAYER, Decimal("5000"))

    page = client.get(r.headers["location"]).text
    assert "Repayment of" in page and "Loan repaid successfully" in page and "Check status" in page
    assert "Loan repayment" in client.get(f"/wallet?msisdn={PAYER}").text


def test_pay_for_self(client, customer, bank):
    client.post("/wallet/deposit", data={"msisdn": BORROWER, "amount": "600"})
    pay(client, payer_msisdn=BORROWER, amount="600")
    assert balance(BORROWER) == 0 and repayments()[0].status == "SUCCESS"
    assert "(self)" in client.get(f"/repay?msisdn={BORROWER}").text


@pytest.mark.parametrize(
    "amount, message",
    [("499", "at least"), ("16801", "at most"), ("0", "whole number"), ("12.5", "whole number"), ("abc", "whole number")],
)
def test_amount_checked_before_calling_bank(client, payer, bank, amount, message):
    r = pay(client, amount=amount)
    assert r.status_code == 400 and message in r.text
    assert bank["requests"] == [] and repayments() == [] and balance(PAYER) == Decimal("10000")


def test_amount_cannot_exceed_outstanding(client, payer, bank):
    r = pay(client, amount="9000", amount_outstanding="8000", max_amount="")
    assert r.status_code == 400 and "cannot exceed the outstanding amount" in r.text


@pytest.mark.parametrize("payer_msisdn, message", [("000", "No customer with MSISDN 000"), ("", "payer MSISDN is required")])
def test_unknown_payer(client, bank, payer_msisdn, message):
    r = pay(client, payer_msisdn=payer_msisdn)
    assert r.status_code == 400 and message in r.text and bank["requests"] == []


def test_insufficient_payer_balance(client, payer, bank):
    r = pay(client, amount="10001", max_amount="20000", amount_outstanding="20000")
    assert r.status_code == 400 and "Insufficient balance" in r.text
    assert bank["requests"] == [] and balance(PAYER) == Decimal("10000")


def test_refused_by_bank_debits_nothing(client, payer, bank):
    bank["responses"]["/repay-loan"] = (200, {"responseCode": "400", "message": "Loan already closed"})
    page = pay(client).text
    assert "Repayment refused by the bank: Loan already closed" in page
    [repayment] = repayments()
    assert (repayment.status, repayment.response_code, repayment.wallet_transaction_id) == ("FAILED", "400", None)
    assert balance(PAYER) == Decimal("10000")


def test_no_answer_is_unknown_then_status_confirms(client, payer, bank):
    bank["responses"]["/repay-loan"] = httpx.ReadTimeout("timed out")
    page = pay(client).text
    assert "No answer from the bank" in page and "Check status" in page
    [repayment] = repayments()
    assert repayment.status == "UNKNOWN" and balance(PAYER) == Decimal("10000")

    bank["responses"]["/repay-loan-status"] = (200, {**REPAID, "data": {**REPAID["data"], "loanfees": "300"}})
    page = client.post(f"/repay/{repayment.id}/status").text
    assert "Repay Loan Status for" in page and "SUCCESS" in page
    assert bank["requests"][-1] == ("/repay-loan-status", {"msisdn": BORROWER, "transactionId": repayment.transaction_id})
    [repayment] = repayments()
    assert (repayment.status, repayment.loan_fees) == ("SUCCESS", Decimal("300"))
    assert repayment.wallet_transaction_id is not None and balance(PAYER) == Decimal("5000")

    # Nouveau Check status : pas de second débit
    client.post(f"/repay/{repayment.id}/status")
    assert balance(PAYER) == Decimal("5000")


def test_status_failed_for_unknown_repayment(client, payer, bank):
    bank["responses"]["/repay-loan"] = httpx.ReadTimeout("timed out")
    pay(client)
    bank["responses"]["/repay-loan-status"] = (200, {"responseCode": "404", "message": "Transaction not found"})
    [repayment] = repayments()
    assert "alert error" in client.post(f"/repay/{repayment.id}/status").text
    assert repayments()[0].status == "FAILED" and balance(PAYER) == Decimal("10000")


def test_status_does_not_downgrade_success(client, payer, bank):
    pay(client)
    bank["responses"]["/repay-loan-status"] = (200, {"responseCode": "500", "message": "oops"})
    [repayment] = repayments()
    client.post(f"/repay/{repayment.id}/status")
    assert repayments()[0].status == "SUCCESS"


def test_status_without_answer_leaves_repayment(client, payer, bank):
    bank["responses"]["/repay-loan"] = httpx.ReadTimeout("timed out")
    pay(client)
    bank["responses"]["/repay-loan-status"] = httpx.ConnectError("refused")
    [repayment] = repayments()
    assert "Repayment unchanged." in client.post(f"/repay/{repayment.id}/status").text
    assert repayments()[0].status == "UNKNOWN"


def test_confirmed_but_payer_cannot_be_debited(client, payer, bank):
    bank["responses"]["/repay-loan"] = httpx.ReadTimeout("timed out")
    pay(client)
    client.post("/wallet/withdraw", data={"msisdn": PAYER, "amount": "8000"})  # le payeur a dépensé entre-temps
    bank["responses"]["/repay-loan-status"] = (200, REPAID)
    [repayment] = repayments()
    page = client.post(f"/repay/{repayment.id}/status").text
    assert "alert warning" in page and "payer wallet was not debited" in page
    [repayment] = repayments()
    assert repayment.status == "SUCCESS" and repayment.wallet_transaction_id is None
    assert balance(PAYER) == Decimal("2000")


def test_status_unknown_repayment_and_missing_paths(client, payer, bank, monkeypatch):
    assert client.post("/repay/999/status").status_code == 404
    pay(client)
    [repayment] = repayments()
    monkeypatch.setattr(get_settings(), "bank_repay_loan_status_path", "")
    assert client.post(f"/repay/{repayment.id}/status").status_code == 503
    monkeypatch.setattr(get_settings(), "bank_repay_loan_path", "")
    assert pay(client).status_code == 503
