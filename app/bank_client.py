"""Appels sortants vers la plateforme de la banque, tous journalisés dans call_sent."""

import json
import secrets
import time
from datetime import datetime

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import CallSent


def send_opt_in(db: Session, url: str, msisdn: str) -> CallSent:
    return _post_json(db, flow="opt_in", url=url, msisdn=msisdn, payload={"msisdn": msisdn})


def send_check_eligibility(db: Session, url: str, msisdn: str) -> CallSent:
    # loanAmount n'est pas obligatoire côté banque : il est envoyé à null
    payload = {"msisdn": msisdn, "loanAmount": None}
    return _post_json(db, flow="check_eligibility", url=url, msisdn=msisdn, payload=payload)


def send_apply_loan(db: Session, url: str, msisdn: str, loan_amount: int, transaction_id: str) -> CallSent:
    payload = {"loanAmount": loan_amount, "msisdn": msisdn, "transactionId": transaction_id}
    return _post_json(db, flow="apply_loan", url=url, msisdn=msisdn, payload=payload)


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
        with httpx.Client(timeout=get_settings().bank_timeout_seconds) as client:
            response = client.post(url, content=body, headers=headers)
        call.response_status = response.status_code
        call.response_headers = dict(response.headers)
        call.response_body = response.text
    except httpx.HTTPError as exc:
        call.error = f"{type(exc).__name__}: {exc}"
    call.duration_ms = int((time.perf_counter() - started) * 1000)

    db.add(call)
    db.commit()
    return call
