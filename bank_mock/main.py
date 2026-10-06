"""Banque simulée : répond aux appels sortants du mock Airtel (opt-in, Check Eligibility, Apply Loan,
Apply Loan Status, Confirm Loan, Cancel Loan).

Les chemins sont provisoires, en attendant la spec de la banque. Les prêts accordés sont conservés dans
un fichier JSON (BANK_MOCK_LOANS_FILE, voir store.py). Les refus métier sont renvoyés en HTTP 200 avec
un responseCode différent de "200", seul champ lu par le mock Airtel.
"""

import asyncio
import json
import logging
import random
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from bank_mock import scenarios, store
from bank_mock.config import get_settings

API_PREFIX = "/api/v1"

# Champs "data" d'un prêt, renvoyés par Apply Loan et Apply Loan Status
LOAN_DATA_KEYS = (
    "transactionId", "loanId", "loanAmount", "loanfees", "outstandingAmount",
    "dueDate", "tenureId", "tenureName", "interestRate",
)
# Issues possibles d'Apply Loan Status pour un prêt accordé et pas encore confirmé ni annulé
STATUS_OUTCOMES = ("BOOKED", "FAILED", "PENDING")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("bank_mock")


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.load()
    logger.info("%s loan(s) loaded from %s", len(store.LOANS), get_settings().loans_file)
    yield


app = FastAPI(
    title="Bank mock",
    description="Simulated partner bank answering the Airtel Money mock. Behaviour depends on the last digit "
    "of the MSISDN: see GET /api/v1/scenarios.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get(API_PREFIX + "/scenarios")
def list_scenarios():
    return {
        "rule": "Last digit of the MSISDN",
        "scenarios": scenarios.DESCRIPTIONS,
        "default": "Success",
    }


@app.get(API_PREFIX + "/loans")
def list_loans(msisdn: str = ""):
    """Prêts accordés depuis le démarrage, pour vérification."""
    return [loan for loan in store.LOANS.values() if not msisdn or loan["msisdn"] == msisdn]


@app.post(API_PREFIX + "/opt-in")
async def opt_in(request: Request):
    body, msisdn, early = await _start(request, "opt_in")
    if early:
        return early

    if scenarios.scenario_for(msisdn) == scenarios.REFUSED:
        return _reply("opt_in", body, {"responseCode": "400", "message": "User opt-in refused", "msisdn": msisdn})
    return _reply("opt_in", body, {"responseCode": "200", "message": "User opted in sucessfully", "msisdn": msisdn})


@app.post(API_PREFIX + "/check-eligibility")
async def check_eligibility(request: Request):
    body, msisdn, early = await _start(request, "check_eligibility")
    if early:
        return early

    if scenarios.scenario_for(msisdn) == scenarios.REFUSED:
        return _reply("check_eligibility", body, {"responseCode": "400", "message": "Customer not eligible"})

    settings = get_settings()
    eligible = _eligible_amount(msisdn)
    return _reply(
        "check_eligibility",
        body,
        {
            "responseCode": "200",
            "message": "Check Eligibility performed sucessfully",
            "data": {
                "msisdn": msisdn,
                "minAmount": str(settings.min_amount),
                "maxAmount": str(settings.max_amount),
                "eligibleAmount": str(eligible),
                "feesAmount": str(_fees(eligible)),
            },
        },
    )


@app.post(API_PREFIX + "/apply-loan")
async def apply_loan(request: Request):
    body, msisdn, early = await _start(request, "apply_loan")
    if early:
        return early

    settings = get_settings()
    scenario = scenarios.scenario_for(msisdn)
    amount = _to_int(body.get("loanAmount"))
    transaction_id = str(body.get("transactionId") or "").strip()

    if not transaction_id:
        return _reply("apply_loan", body, {"responseCode": "400", "message": "transactionId is required"})
    if transaction_id in store.LOANS:
        return _reply("apply_loan", body, {"responseCode": "409", "message": "Duplicate transactionId"})
    if scenario == scenarios.REFUSED:
        return _reply("apply_loan", body, {"responseCode": "400", "message": "Customer not eligible"})
    if scenario == scenarios.ACTIVE_LOAN:
        return _reply("apply_loan", body, {"responseCode": "409", "message": "Customer has an active loan"})
    if amount is None or not settings.min_amount <= amount <= _eligible_amount(msisdn):
        return _reply("apply_loan", body, {"responseCode": "400", "message": "Invalid loan amount"})

    fees = _fees(amount)
    due_date = datetime.now().replace(hour=23, minute=59, second=59, microsecond=0) + timedelta(
        days=settings.tenure_days
    )
    loan = {
        "transactionId": transaction_id,
        "loanId": f"{secrets.randbelow(10**17):017d}",
        "loanAmount": amount,
        "loanfees": str(fees),
        "outstandingAmount": str(amount + fees),
        "dueDate": f"{due_date:%Y-%m-%d %H:%M:%S}",
        "tenureId": str(body.get("tenureId") or settings.tenure_days),
        "tenureName": f"{settings.tenure_days} days",
        "interestRate": str(settings.interest_rate),
    }
    store.LOANS[transaction_id] = {"msisdn": msisdn, **loan, "status": "BOOKED", "externalTransactionId": None}
    store.save()
    return _reply("apply_loan", body, {"responseCode": "200", "message": "Loan booked sucessfully", "data": loan})


@app.post(API_PREFIX + "/apply-loan-status")
async def apply_loan_status(request: Request):
    """Airtel n'a pas reçu la réponse d'Apply Loan (timeout) et demande l'issue de la demande.

    Pour un prêt accordé ni confirmé ni annulé, l'issue est tirée au hasard (STATUS_OUTCOMES), sauf si
    BANK_MOCK_LOAN_STATUS l'impose. Un tirage FAILED est définitif : le prêt ne peut plus être confirmé.
    """
    body, msisdn, early = await _start(request, "apply_loan_status")
    if early:
        return early

    transaction_id = str(body.get("transactionId") or "").strip()
    loan = store.LOANS.get(transaction_id)

    if not transaction_id:
        return _reply("apply_loan_status", body, {"responseCode": "400", "message": "transactionId is required"})
    if loan is None or loan["msisdn"] != msisdn:
        return _reply("apply_loan_status", body, {"responseCode": "404", "message": "Loan not found"})

    status = loan["status"]
    if status == "BOOKED":
        forced = get_settings().loan_status.strip().upper()
        status = forced if forced in STATUS_OUTCOMES else random.choice(STATUS_OUTCOMES)
        logger.info("apply_loan_status: %s -> outcome %s", transaction_id, status)
        if status == "FAILED":
            loan["status"] = "FAILED"
            store.save()

    if status in ("BOOKED", "CONFIRMED"):
        data = {key: loan[key] for key in LOAN_DATA_KEYS}
        content = {"responseCode": "200", "message": "Loan booked sucessfully", "data": data}
    elif status == "PENDING":
        content = {"responseCode": "202", "message": "Loan request in progress"}
    elif status == "CANCELLED":
        content = {"responseCode": "409", "message": "Loan is cancelled"}
    else:
        content = {"responseCode": "400", "message": "Loan request failed"}
    return _reply("apply_loan_status", body, content)


@app.post(API_PREFIX + "/confirm-loan")
async def confirm_loan(request: Request):
    """Airtel confirme que le prêt a été décaissé sur le compte Mobile Money du client."""
    body, msisdn, early = await _start(request, "confirm_loan")
    if early:
        return early

    transaction_id = str(body.get("transactionId") or "").strip()
    external_transaction_id = str(body.get("externalTransactionId") or "").strip()
    loan = store.LOANS.get(transaction_id)

    if not transaction_id or not external_transaction_id:
        message = "transactionId and externalTransactionId are required"
        return _reply("confirm_loan", body, {"responseCode": "400", "message": message})
    if loan is None or loan["msisdn"] != msisdn:
        return _reply("confirm_loan", body, {"responseCode": "404", "message": "Loan not found"})
    if str(body.get("loanId") or "").strip() != loan["loanId"]:
        return _reply("confirm_loan", body, {"responseCode": "400", "message": "loanId does not match"})
    if _to_int(body.get("loanAmount")) != loan["loanAmount"]:
        return _reply("confirm_loan", body, {"responseCode": "400", "message": "loanAmount does not match"})
    if loan["status"] == "CONFIRMED":
        return _reply("confirm_loan", body, {"responseCode": "409", "message": "Loan already confirmed"})
    if loan["status"] == "CANCELLED":
        return _reply("confirm_loan", body, {"responseCode": "409", "message": "Loan is cancelled"})
    if loan["status"] == "FAILED":
        return _reply("confirm_loan", body, {"responseCode": "409", "message": "Loan request failed"})

    loan["status"] = "CONFIRMED"
    loan["externalTransactionId"] = external_transaction_id
    store.save()
    return _reply("confirm_loan", body, {"responseCode": "200", "message": "Loan confirmed sucessfully"})


@app.post(API_PREFIX + "/cancel-loan")
async def cancel_loan(request: Request):
    """Airtel n'a pas pu verser le prêt sur le compte Mobile Money : le prêt est annulé."""
    body, msisdn, early = await _start(request, "cancel_loan")
    if early:
        return early

    transaction_id = str(body.get("transactionId") or "").strip()
    loan = store.LOANS.get(transaction_id)

    if not transaction_id:
        return _reply("cancel_loan", body, {"responseCode": "400", "message": "transactionId is required"})
    if loan is None or loan["msisdn"] != msisdn:
        return _reply("cancel_loan", body, {"responseCode": "404", "message": "Loan not found"})
    if loan["status"] == "CONFIRMED":
        return _reply("cancel_loan", body, {"responseCode": "409", "message": "Loan already confirmed"})
    if loan["status"] == "CANCELLED":
        return _reply("cancel_loan", body, {"responseCode": "409", "message": "Loan already cancelled"})
    if loan["status"] == "FAILED":
        return _reply("cancel_loan", body, {"responseCode": "409", "message": "Loan request failed"})

    loan["status"] = "CANCELLED"
    store.save()
    return _reply(
        "cancel_loan", body, {"responseCode": "200", "message": "Loan Cancelled sucessfully", "msisdn": msisdn}
    )


async def _start(request: Request, flow: str) -> tuple[dict, str, Response | None]:
    """Lit le corps, vérifie le MSISDN et applique les scénarios techniques (lenteur, erreur 500).

    Renvoie (corps, msisdn, réponse anticipée ou None).
    """
    raw = await request.body()
    try:
        body = json.loads(raw)
    except ValueError:
        body = None
    if not isinstance(body, dict):
        logger.info("%s <- %s", flow, raw.decode("utf-8", errors="replace"))
        return {}, "", _reply(flow, None, {"responseCode": "400", "message": "Malformed request body"}, 400)

    msisdn = str(body.get("msisdn") or "").strip()
    if not (len(msisdn) == 9 and msisdn.isdigit()):
        return body, msisdn, _reply(flow, body, {"responseCode": "400", "message": "msisdn must be 9 digits"})

    scenario = scenarios.scenario_for(msisdn)
    if scenario == scenarios.SLOW:
        delay = get_settings().slow_seconds
        logger.info("%s: msisdn %s, waiting %ss before answering", flow, msisdn, delay)
        await asyncio.sleep(delay)
    elif scenario == scenarios.SERVER_ERROR:
        logger.info("%s <- %s", flow, json.dumps(body))
        logger.info("%s -> 500 Internal Server Error", flow)
        return body, msisdn, PlainTextResponse("Internal Server Error", status_code=500)
    return body, msisdn, None


def _reply(flow: str, request_body: dict | None, content: dict, status_code: int = 200) -> JSONResponse:
    if request_body is not None:
        logger.info("%s <- %s", flow, json.dumps(request_body))
    logger.info("%s -> %s %s", flow, status_code, json.dumps(content))
    return JSONResponse(content, status_code=status_code)


def _eligible_amount(msisdn: str) -> int:
    settings = get_settings()
    if scenarios.scenario_for(msisdn) == scenarios.BELOW_MINIMUM:
        return settings.min_amount // 2
    return settings.eligible_amount


def _fees(amount: int) -> int:
    return amount * get_settings().interest_rate // 100


def _to_int(value) -> int | None:
    """Accepte un entier ou une chaîne numérique entière ("20000")."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation:
        return None
    if not number.is_finite() or number != number.to_integral_value():
        return None
    return int(number)
