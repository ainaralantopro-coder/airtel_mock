from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import bank_client
from app.config import get_settings
from app.database import SessionLocal
from app.models import Customer, Loan
from app.web import format_ariary
from bank_mock import main as bank_main
from bank_mock.config import get_settings as get_bank_settings

OK = "997739690"
REFUSED = "997739691"
BELOW_MINIMUM = "997739692"
SLOW = "997739693"
SERVER_ERROR = "997739694"
ACTIVE_LOAN = "997739695"


@pytest.fixture
def bank():
    bank_main.LOANS.clear()
    with TestClient(bank_main.app) as c:
        yield c
    bank_main.LOANS.clear()


def apply(bank, msisdn=OK, amount=20000, transaction_id="APC1"):
    body = {"loanAmount": amount, "msisdn": msisdn, "transactionId": transaction_id}
    return bank.post("/api/v1/apply-loan", json=body).json()


# --- Opt-in -----------------------------------------------------------------

def test_opt_in_success(bank):
    r = bank.post("/api/v1/opt-in", json={"msisdn": OK})
    assert r.status_code == 200
    assert r.json() == {"responseCode": "200", "message": "User opted in sucessfully", "msisdn": OK}


def test_opt_in_refused(bank):
    assert bank.post("/api/v1/opt-in", json={"msisdn": REFUSED}).json()["responseCode"] == "400"


@pytest.mark.parametrize("body", [{"msisdn": "12345"}, {}, {"msisdn": None}])
def test_invalid_msisdn(bank, body):
    r = bank.post("/api/v1/opt-in", json=body)
    assert r.json() == {"responseCode": "400", "message": "msisdn must be 9 digits"}


def test_malformed_body(bank):
    r = bank.post("/api/v1/opt-in", content="not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 400
    assert r.json()["responseCode"] == "400"


def test_server_error_scenario(bank):
    r = bank.post("/api/v1/check-eligibility", json={"msisdn": SERVER_ERROR})
    assert r.status_code == 500
    assert r.text == "Internal Server Error"


def test_slow_scenario_waits_then_answers(bank, monkeypatch):
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(bank_main.asyncio, "sleep", fake_sleep)
    r = bank.post("/api/v1/opt-in", json={"msisdn": SLOW})
    assert slept == [get_bank_settings().slow_seconds]
    assert r.json()["responseCode"] == "200"


# --- Check Eligibility ------------------------------------------------------

def test_eligibility_success(bank):
    r = bank.post("/api/v1/check-eligibility", json={"msisdn": OK, "loanAmount": None})
    assert r.json() == {
        "responseCode": "200",
        "message": "Check Eligibility performed sucessfully",
        "data": {
            "msisdn": OK,
            "minAmount": "5000",
            "maxAmount": "100000",
            "eligibleAmount": "50000",
            "feesAmount": "1000",
        },
    }


def test_eligibility_refused(bank):
    body = bank.post("/api/v1/check-eligibility", json={"msisdn": REFUSED}).json()
    assert body == {"responseCode": "400", "message": "Customer not eligible"}


def test_eligibility_below_minimum(bank):
    data = bank.post("/api/v1/check-eligibility", json={"msisdn": BELOW_MINIMUM}).json()["data"]
    assert int(data["eligibleAmount"]) < int(data["minAmount"])


# --- Apply Loan -------------------------------------------------------------

def test_apply_loan_success(bank):
    body = apply(bank, amount="20000")
    assert body["responseCode"] == "200" and body["message"] == "Loan booked sucessfully"
    data = body["data"]
    assert (data["transactionId"], data["loanAmount"], data["loanfees"], data["outstandingAmount"]) == (
        "APC1", 20000, "400", "20400"
    )
    assert (data["tenureId"], data["tenureName"], data["interestRate"]) == ("30", "30 days", "2")
    assert len(data["loanId"]) == 17 and data["loanId"].isdigit()
    expected_due = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d") + " 23:59:59"
    assert data["dueDate"] == expected_due

    [loan] = bank.get("/api/v1/loans", params={"msisdn": OK}).json()
    assert loan["loanId"] == data["loanId"]
    assert bank.get("/api/v1/loans", params={"msisdn": REFUSED}).json() == []


@pytest.mark.parametrize(
    "msisdn, amount, transaction_id, code, message",
    [
        (OK, 4999, "APC1", "400", "Invalid loan amount"),
        (OK, 50001, "APC1", "400", "Invalid loan amount"),
        (OK, "abc", "APC1", "400", "Invalid loan amount"),
        (OK, 20000, "", "400", "transactionId is required"),
        (REFUSED, 20000, "APC1", "400", "Customer not eligible"),
        (BELOW_MINIMUM, 5000, "APC1", "400", "Invalid loan amount"),
        (ACTIVE_LOAN, 20000, "APC1", "409", "Customer has an active loan"),
    ],
)
def test_apply_loan_refused(bank, msisdn, amount, transaction_id, code, message):
    body = apply(bank, msisdn, amount, transaction_id)
    assert (body["responseCode"], body["message"]) == (code, message)
    assert bank.get("/api/v1/loans").json() == []


def test_apply_loan_duplicate_transaction(bank):
    apply(bank)
    assert apply(bank)["message"] == "Duplicate transactionId"


# --- Confirm Loan -----------------------------------------------------------

def confirm(bank, **overrides):
    loan = bank_main.LOANS["APC1"]
    body = {"msisdn": OK, "transactionId": "APC1", "loanAmount": loan["loanAmount"], "loanId": loan["loanId"],
            "externalTransactionId": "APC9", **overrides}
    return bank.post("/api/v1/confirm-loan", json=body).json()


def test_confirm_loan_success(bank):
    apply(bank)
    assert confirm(bank) == {"responseCode": "200", "message": "Loan confirmed sucessfully"}
    [loan] = bank.get("/api/v1/loans").json()
    assert (loan["status"], loan["externalTransactionId"]) == ("CONFIRMED", "APC9")
    assert confirm(bank) == {"responseCode": "409", "message": "Loan already confirmed"}


@pytest.mark.parametrize(
    "overrides, code, message",
    [
        ({"transactionId": "UNKNOWN"}, "404", "Loan not found"),
        ({"msisdn": "997739698"}, "404", "Loan not found"),
        ({"loanId": "123"}, "400", "loanId does not match"),
        ({"loanAmount": 1}, "400", "loanAmount does not match"),
        ({"externalTransactionId": ""}, "400", "transactionId and externalTransactionId are required"),
    ],
)
def test_confirm_loan_refused(bank, overrides, code, message):
    apply(bank)
    assert confirm(bank, **overrides) == {"responseCode": code, "message": message}
    assert bank_main.LOANS["APC1"]["status"] == "BOOKED"


def test_scenarios_listed(bank):
    assert set(bank.get("/api/v1/scenarios").json()["scenarios"]) == {"1", "2", "3", "4", "5"}


# --- Bout en bout : mock Airtel -> bank-mock ---------------------------------

@pytest.fixture
def airtel_to_bank(bank, monkeypatch):
    """Les appels sortants du mock Airtel arrivent sur le bank-mock, avec les chemins provisoires."""
    settings = get_settings()
    monkeypatch.setattr(settings, "bank_opt_in_path", "/api/v1/opt-in")
    monkeypatch.setattr(settings, "bank_check_eligibility_path", "/api/v1/check-eligibility")
    monkeypatch.setattr(settings, "bank_apply_loan_path", "/api/v1/apply-loan")
    monkeypatch.setattr(settings, "bank_confirm_loan_path", "/api/v1/confirm-loan")
    monkeypatch.setattr(bank_client.httpx, "Client", lambda **kw: TestClient(bank_main.app))


def test_end_to_end_opt_in_and_loan(client, customer, airtel_to_bank):
    r = client.post("/register", data={"msisdn": customer})
    assert "opted in." in r.text
    with SessionLocal() as db:
        assert db.scalar(select(Customer.opted_in).where(Customer.msisdn == customer)) is True

    r = client.post("/loans/borrow/eligibility", data={"msisdn": OK})
    assert f"Eligible amount: <strong>{format_ariary(50000)}</strong>" in r.text

    form = {"msisdn": OK, "min_amount": "5000", "max_amount": "100000", "eligible_amount": "50000",
            "fees_amount": "1000", "amount": "20000"}
    r = client.post("/loans/borrow/apply", data=form)
    assert "Loan booked sucessfully" in r.text
    with SessionLocal() as db:
        loan = db.scalar(select(Loan))
    assert loan.status == "BOOKED"
    assert bank_main.LOANS[loan.transaction_id]["loanId"] == loan.loan_id

    r = client.post(f"/loans/{loan.id}/disburse")
    assert "Loan confirmed sucessfully" in r.text
    assert bank_main.LOANS[loan.transaction_id]["status"] == "CONFIRMED"
    with SessionLocal() as db:
        assert db.get(Loan, loan.id).is_disbursed is True
