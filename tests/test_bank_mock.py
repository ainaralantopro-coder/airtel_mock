import json
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
from bank_mock import store
from bank_mock.config import get_settings as get_bank_settings

OK = "997739690"
REFUSED = "997739691"
BELOW_MINIMUM = "997739692"
SLOW = "997739693"
SERVER_ERROR = "997739694"
ACTIVE_LOAN = "997739695"


@pytest.fixture
def loans_file(tmp_path, monkeypatch):
    path = tmp_path / "loans.json"
    monkeypatch.setattr(get_bank_settings(), "loans_file", str(path))
    return path


@pytest.fixture
def bank(loans_file):
    with TestClient(bank_main.app) as c:
        yield c
    store.LOANS.clear()


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
    loan = store.LOANS["APC1"]
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
    assert store.LOANS["APC1"]["status"] == "BOOKED"


# --- Apply Loan Status ------------------------------------------------------

def loan_status(bank, msisdn=OK, transaction_id="APC1"):
    return bank.post("/api/v1/apply-loan-status", json={"msisdn": msisdn, "transactionId": transaction_id}).json()


@pytest.fixture
def outcome(monkeypatch):
    """Impose l'issue du tirage aléatoire d'Apply Loan Status."""
    def force(value):
        monkeypatch.setattr(bank_main.random, "choice", lambda options: value)
    return force


def test_loan_status_booked(bank, outcome):
    booked = apply(bank)["data"]
    outcome("BOOKED")
    assert loan_status(bank) == {"responseCode": "200", "message": "Loan booked sucessfully", "data": booked}
    assert store.LOANS["APC1"]["status"] == "BOOKED"


def test_loan_status_pending_is_redrawn(bank, outcome):
    apply(bank)
    outcome("PENDING")
    assert loan_status(bank) == {"responseCode": "202", "message": "Loan request in progress"}
    outcome("BOOKED")
    assert loan_status(bank)["responseCode"] == "200"


def test_loan_status_failed_is_final(bank, outcome, loans_file):
    apply(bank)
    outcome("FAILED")
    assert loan_status(bank) == {"responseCode": "400", "message": "Loan request failed"}
    assert json.loads(loans_file.read_text(encoding="utf-8"))["APC1"]["status"] == "FAILED"
    outcome("BOOKED")
    assert loan_status(bank)["responseCode"] == "400"
    assert confirm(bank) == {"responseCode": "409", "message": "Loan request failed"}
    assert cancel(bank) == {"responseCode": "409", "message": "Loan request failed"}


def test_loan_status_random_draws_among_outcomes(bank):
    apply(bank)
    codes = {loan_status(bank)["responseCode"] for _ in range(30)}
    assert codes and codes <= {"200", "202", "400"}


def test_loan_status_forced_by_setting(bank, monkeypatch):
    apply(bank)
    monkeypatch.setattr(get_bank_settings(), "loan_status", "pending")
    assert loan_status(bank)["responseCode"] == "202"
    monkeypatch.setattr(get_bank_settings(), "loan_status", "nonsense")
    assert loan_status(bank)["responseCode"] in {"200", "202", "400"}


def test_loan_status_after_confirm_or_cancel(bank, outcome):
    outcome("FAILED")  # ignoré : pas de tirage une fois le prêt confirmé ou annulé
    apply(bank)
    confirm(bank)
    assert loan_status(bank)["responseCode"] == "200"
    apply(bank, transaction_id="APC2")
    cancel(bank, transaction_id="APC2")
    assert loan_status(bank, transaction_id="APC2") == {"responseCode": "409", "message": "Loan is cancelled"}


@pytest.mark.parametrize(
    "msisdn, transaction_id, code",
    [(OK, "UNKNOWN", "404"), ("997739698", "APC1", "404"), (OK, "", "400")],
)
def test_loan_status_refused(bank, msisdn, transaction_id, code):
    apply(bank)
    assert loan_status(bank, msisdn, transaction_id)["responseCode"] == code


def test_end_to_end_timeout_after_lost_apply_response(client, airtel_to_bank, outcome, monkeypatch):
    """La banque accorde le prêt mais sa réponse est perdue : le mock Airtel le note FAILED, puis le bouton
    Timeout (Apply Loan Status) le récupère."""
    bank_client_factory = bank_client.httpx.Client

    def losing_apply_response(**kw):
        http = bank_client_factory(**kw)
        original_post = http.post

        def post(url, **kwargs):
            response = original_post(url, **kwargs)
            if url.endswith("/apply-loan"):
                raise bank_client.httpx.ReadTimeout("timed out")
            return response
        http.post = post
        return http

    monkeypatch.setattr(bank_client.httpx, "Client", losing_apply_response)
    form = {"msisdn": OK, "min_amount": "5000", "max_amount": "100000", "eligible_amount": "50000",
            "fees_amount": "1000", "amount": "20000"}
    client.post("/loans/borrow/apply", data=form)
    with SessionLocal() as db:
        loan = db.scalar(select(Loan))
    assert loan.status == "FAILED" and store.LOANS[loan.transaction_id]["status"] == "BOOKED"

    outcome("BOOKED")
    assert "Disburse OK" in client.post(f"/loans/{loan.id}/status").text
    with SessionLocal() as db:
        loan = db.get(Loan, loan.id)
    assert loan.status == "BOOKED" and loan.loan_id == store.LOANS[loan.transaction_id]["loanId"]


# --- Cancel Loan ------------------------------------------------------------

def cancel(bank, msisdn=OK, transaction_id="APC1"):
    return bank.post("/api/v1/cancel-loan", json={"msisdn": msisdn, "transactionId": transaction_id}).json()


def test_cancel_loan_success(bank, loans_file):
    apply(bank)
    assert cancel(bank) == {"responseCode": "200", "message": "Loan Cancelled sucessfully", "msisdn": OK}
    assert json.loads(loans_file.read_text(encoding="utf-8"))["APC1"]["status"] == "CANCELLED"
    assert cancel(bank) == {"responseCode": "409", "message": "Loan already cancelled"}
    assert confirm(bank) == {"responseCode": "409", "message": "Loan is cancelled"}


def test_cancel_confirmed_loan_refused(bank):
    apply(bank)
    confirm(bank)
    assert cancel(bank) == {"responseCode": "409", "message": "Loan already confirmed"}
    assert store.LOANS["APC1"]["status"] == "CONFIRMED"


@pytest.mark.parametrize(
    "msisdn, transaction_id, code, message",
    [
        (OK, "UNKNOWN", "404", "Loan not found"),
        ("997739698", "APC1", "404", "Loan not found"),
        (OK, "", "400", "transactionId is required"),
    ],
)
def test_cancel_loan_refused(bank, msisdn, transaction_id, code, message):
    apply(bank)
    assert cancel(bank, msisdn, transaction_id) == {"responseCode": code, "message": message}
    assert store.LOANS["APC1"]["status"] == "BOOKED"


def test_end_to_end_disburse_ko(client, airtel_to_bank):
    form = {"msisdn": OK, "min_amount": "5000", "max_amount": "100000", "eligible_amount": "50000",
            "fees_amount": "1000", "amount": "20000"}
    client.post("/loans/borrow/apply", data=form)
    with SessionLocal() as db:
        loan = db.scalar(select(Loan))
    r = client.post(f"/loans/{loan.id}/cancel")
    assert "Loan Cancelled sucessfully" in r.text
    assert store.LOANS[loan.transaction_id]["status"] == "CANCELLED"
    with SessionLocal() as db:
        assert db.get(Loan, loan.id).status == "CANCELLED"


# --- Persistance JSON -------------------------------------------------------

def test_loans_survive_restart(bank, loans_file):
    booked = apply(bank)["data"]
    saved = json.loads(loans_file.read_text(encoding="utf-8"))
    assert saved["APC1"]["loanId"] == booked["loanId"] and saved["APC1"]["status"] == "BOOKED"

    store.LOANS.clear()
    with TestClient(bank_main.app) as restarted:
        assert confirm(restarted)["responseCode"] == "200"
    assert json.loads(loans_file.read_text(encoding="utf-8"))["APC1"]["status"] == "CONFIRMED"
    assert not loans_file.with_name("loans.json.tmp").exists()


def test_corrupt_loans_file_stops_startup(loans_file):
    loans_file.write_text("{not json", encoding="utf-8")
    with pytest.raises(RuntimeError, match="not valid JSON"):
        with TestClient(bank_main.app):
            pass


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
    monkeypatch.setattr(settings, "bank_apply_loan_status_path", "/api/v1/apply-loan-status")
    monkeypatch.setattr(settings, "bank_confirm_loan_path", "/api/v1/confirm-loan")
    monkeypatch.setattr(settings, "bank_cancel_loan_path", "/api/v1/cancel-loan")
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
    assert store.LOANS[loan.transaction_id]["loanId"] == loan.loan_id

    r = client.post(f"/loans/{loan.id}/disburse")
    assert "Loan confirmed sucessfully" in r.text
    assert store.LOANS[loan.transaction_id]["status"] == "CONFIRMED"
    with SessionLocal() as db:
        assert db.get(Loan, loan.id).is_disbursed is True
