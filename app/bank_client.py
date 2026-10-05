"""Appels sortants vers la plateforme de la banque, tous journalisés dans call_sent."""

import json
import time

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import CallSent


def send_opt_in(db: Session, url: str, msisdn: str) -> CallSent:
    return _post_json(db, flow="opt_in", url=url, msisdn=msisdn, payload={"msisdn": msisdn})


def is_opt_in_success(call: CallSent) -> bool:
    """Le client n'est opted-in que si la banque répond avec "responseCode": "200".
    Les autres champs de la réponse sont ignorés."""
    if call.error or not call.response_body:
        return False
    try:
        body = json.loads(call.response_body)
    except ValueError:
        return False
    return isinstance(body, dict) and str(body.get("responseCode")) == "200"


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
