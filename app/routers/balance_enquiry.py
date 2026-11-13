"""Écran Balance enquiry : demande à la banque le solde d'un client (appel sortant).

À ne pas confondre avec GET /standard/v2/users/{msisdn}/balance, que la banque appelle chez Airtel.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from starlette.datastructures import FormData
from sqlalchemy.orm import Session

from app.bank_client import is_success, response_json, send_balance_enquiry
from app.config import get_settings
from app.database import get_db
from app.routers.loans import _bank_error, _to_decimal, _to_str
from app.web import read_form, templates

router = APIRouter(prefix="/balance-enquiry", include_in_schema=False)


@router.get("", response_class=HTMLResponse)
def balance_form(request: Request):
    return templates.TemplateResponse(request, "balance_enquiry.html", _context(""))


@router.post("", response_class=HTMLResponse)
def balance_enquiry(request: Request, form: FormData = Depends(read_form), db: Session = Depends(get_db)):
    msisdn = str(form.get("msisdn", "")).strip()
    context = _context(msisdn)

    if context["url"] is None:
        context["error"] = "BANK_BALANCE_ENQUIRY_PATH must be set in .env."
        return templates.TemplateResponse(request, "balance_enquiry.html", context, status_code=503)
    if not msisdn:
        context["error"] = "The MSISDN is required."
        return templates.TemplateResponse(request, "balance_enquiry.html", context, status_code=400)

    call = send_balance_enquiry(db, context["url"], msisdn)
    body = response_json(call)
    context["call"] = call
    if not is_success(body):
        context["error"] = _bank_error(call, body)
    else:
        balance = body.get("balance")
        context["balance"] = _to_decimal(balance)
        context["balance_text"] = _to_str(balance)  # affiché tel quel s'il n'est pas numérique
        context["message"] = _to_str(body.get("message"))
    return templates.TemplateResponse(request, "balance_enquiry.html", context)


def _context(msisdn: str) -> dict:
    return {"msisdn": msisdn, "url": get_settings().bank_balance_enquiry_url}
