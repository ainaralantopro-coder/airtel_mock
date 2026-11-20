"""Appels sortants vers la plateforme de la banque, tous journalisés dans call_sent."""

import json
import secrets
import time
from datetime import datetime

import httpx
from sqlalchemy.orm import Session

from app import bank_auth
from app.config import get_settings
from app.models import CallSent

PENDING_RESPONSE_CODE = "202"


def send_opt_in(db: Session, url: str, msisdn: str) -> CallSent:
    return _post_json(db, flow="opt_in", url=url, msisdn=msisdn, payload={"msisdn": msisdn})


def send_check_eligibility(db: Session, url: str, msisdn: str) -> CallSent:
    payload = {"msisdn": msisdn}
    return _post_json(db, flow="check_eligibility", url=url, msisdn=msisdn, payload=payload)


def send_apply_loan(db: Session, url: str, msisdn: str, loan_amount: int, transaction_id: str) -> CallSent:
    payload = {"loanAmount": loan_amount, "msisdn": msisdn, "transactionId": transaction_id}
    return _post_json(db, flow="apply_loan", url=url, msisdn=msisdn, payload=payload)


def send_apply_loan_status(db: Session, url: str, msisdn: str, transaction_id: str) -> CallSent:
    payload = {"msisdn": msisdn, "transactionId": transaction_id}
    return _post_json(db, flow="apply_loan_status", url=url, msisdn=msisdn, payload=payload)


def send_confirm_loan(
    db: Session, url: str, msisdn: str, transaction_id: str, loan_amount: int, loan_id: str,
    external_transaction_id: str,
) -> CallSent:
    payload = {
        "msisdn": msisdn,
        "transactionId": transaction_id,
        "loanAmount": loan_amount,
        "loanId": loan_id,
        "externalTransactionId": external_transaction_id,
    }
    return _post_json(db, flow="confirm_loan", url=url, msisdn=msisdn, payload=payload)


def send_cancel_loan(db: Session, url: str, msisdn: str, transaction_id: str) -> CallSent:
    payload = {"msisdn": msisdn, "transactionId": transaction_id}
    return _post_json(db, flow="cancel_loan", url=url, msisdn=msisdn, payload=payload)


def send_topup_notification(
    db: Session, url: str, msisdn: str, top_up_amount: str, transaction_id: str, transaction_timestamp: str,
    available_balance_amount: str,
) -> CallSent:
    payload = {
        "msisdn": msisdn,
        "topUpAmount": top_up_amount,
        "transactionId": transaction_id,
        "transactionTimestamp": transaction_timestamp,
        "availableBalanceAmount": available_balance_amount,
    }
    return _post_json(db, flow="topup_notification", url=url, msisdn=msisdn, payload=payload)


def send_lookup_details(db: Session, url: str, msisdn: str) -> CallSent:
    return _post_json(db, flow="lookup_details", url=url, msisdn=msisdn, payload={"msisdn": msisdn})


def send_repay_loan(
    db: Session, url: str, msisdn: str, repayment_amount: int, transaction_id: str, loan_id: str
) -> CallSent:
    payload = {
        "repaymentAmount": repayment_amount,
        "msisdn": msisdn,
        "transactionId": transaction_id,
        "loanId": loan_id,
    }

    return _post_json(db, flow="repay_loan", url=url, msisdn=msisdn, payload=payload)


def send_repay_loan_status(db: Session, url: str, msisdn: str, transaction_id: str) -> CallSent:
    payload = {"msisdn": msisdn, "transactionId": transaction_id}
    return _post_json(db, flow="repay_loan_status", url=url, msisdn=msisdn, payload=payload)


def send_mini_statement(db: Session, url: str, msisdn: str) -> CallSent:
    return _post_json(db, flow="mini_statement", url=url, msisdn=msisdn, payload={"msisdn": msisdn})


def send_balance_enquiry(db: Session, url: str, msisdn: str) -> CallSent:
    return _post_json(db, flow="balance_enquiry", url=url, msisdn=msisdn, payload={"msisdn": msisdn})


def new_transaction_id() -> str:
    """Identifiant de transaction côté Airtel : APC + horodatage + 8 chiffres aléatoires."""
    return f"APC{datetime.now():%Y%m%d%H%M%S}{secrets.randbelow(10**8):08d}"


def response_json(call: CallSent) -> dict | None:
    """Corps JSON de la réponse de la banque, ou None si l'appel a échoué ou si ce n'est pas un objet JSON."""
    if call.error or not call.response_body:
        return None
    try:
        body = json.loads(call.response_body)
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def is_pending(body: dict | None) -> bool:
    """Demande encore en cours à la banque. Code provisoire, absent de la spec."""
    return body is not None and str(body.get("responseCode")) == PENDING_RESPONSE_CODE


def is_success(body: dict | None) -> bool:
    """Seul "responseCode": "200" compte, les autres champs sont ignorés."""
    return body is not None and str(body.get("responseCode")) == "200"


def is_opt_in_success(call: CallSent) -> bool:
    return is_success(response_json(call))


def _post_json(db: Session, flow: str, url: str, msisdn: str | None, payload: dict) -> CallSent:
    body = json.dumps(payload)
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    call = CallSent(
        flow=flow,
        msisdn=msisdn,
        method="POST",
        url=url,
        request_headers=headers,
        request_body=body,
    )

    started = time.perf_counter()
    try:
        response = _send(url, body, headers)
        call.response_status = response.status_code
        call.response_headers = dict(response.headers)
        call.response_body = response.text
    except bank_auth.TokenError as exc:
        call.error = f"Token request failed: {exc}"
    except httpx.HTTPError as exc:
        call.error = f"{type(exc).__name__}: {exc}"
    if any(name.lower() == "authorization" for name in headers):
        call.request_headers = {**headers, "Authorization": "Bearer ***"}  # le token n'est pas journalisé
    call.duration_ms = int((time.perf_counter() - started) * 1000)

    db.add(call)
    db.commit()
    return call


def _send(url: str, body: str, headers: dict) -> httpx.Response:
    """POST avec le token de la banque. Un 401 (token révoqué ou expiré plus tôt que prévu) est retenté une fois
    avec un nouveau token. Complète headers avec Authorization."""
    with httpx.Client(timeout=get_settings().bank_timeout_seconds) as client:
        authorization = bank_auth.authorization_header()
        if authorization is None:
            return client.post(url, content=body, headers=headers)
        headers["Authorization"] = authorization
        response = client.post(url, content=body, headers=headers)
        if response.status_code == 401:
            bank_auth.invalidate()
            headers["Authorization"] = bank_auth.authorization_header()
            response = client.post(url, content=body, headers=headers)
        return response
