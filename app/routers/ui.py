"""Écrans du mock : clients, enregistrement (opt-in) et messages reçus."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bank_client import is_opt_in_success, send_opt_in
from app.config import get_settings
from app.database import get_db
from app.models import Customer, MessageSent
from app.schemas import CustomerCreate, is_valid_msisdn
from app.web import templates

router = APIRouter(include_in_schema=False)

FIELD_LABELS = {
    "msisdn": "MSISDN",
    "first_name": "First name",
    "last_name": "Last name",
    "dob": "Date of birth",
    "id_number": "ID number",
    "grade": "Grade",
    "account_status": "Account status",
    "nationality": "Nationality",
    "registration_status": "Registration status",
}


@router.get("/", response_class=HTMLResponse)
def customer_list(request: Request, db: Session = Depends(get_db)):
    customers = db.scalars(select(Customer).order_by(Customer.created_at.desc())).all()
    return templates.TemplateResponse(request, "customers.html", {"customers": customers})


@router.get("/customers/new", response_class=HTMLResponse)
def customer_form(request: Request):
    return templates.TemplateResponse(request, "customer_form.html", {"values": {}, "errors": []})


@router.post("/customers/new", response_class=HTMLResponse)
async def customer_create(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    values = {key: value for key, value in form.items() if isinstance(value, str)}
    data = {**values, "is_barred": "is_barred" in form, "is_pin_set": "is_pin_set" in form}

    errors: list[str] = []
    try:
        customer_in = CustomerCreate.model_validate(data)
    except ValidationError as exc:
        errors = [_format_form_error(e) for e in exc.errors()]
    else:
        if db.scalar(select(Customer.id).where(Customer.msisdn == customer_in.msisdn)):
            errors = [f"A customer with MSISDN {customer_in.msisdn} already exists."]

    if errors:
        context = {"values": data, "errors": errors}
        return templates.TemplateResponse(request, "customer_form.html", context, status_code=400)

    payload = customer_in.model_dump()
    payload["dob"] = datetime.combine(payload["dob"], datetime.min.time())
    db.add(Customer(**payload))
    db.commit()
    return RedirectResponse("/?created=" + customer_in.msisdn, status_code=303)


@router.get("/register", response_class=HTMLResponse)
def register_form(request: Request, msisdn: str = ""):
    context = {"msisdn": msisdn, "opt_in_url": get_settings().bank_opt_in_url}
    return templates.TemplateResponse(request, "register.html", context)


@router.post("/register", response_class=HTMLResponse)
async def register_submit(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    msisdn = str(form.get("msisdn", "")).strip()
    opt_in_url = get_settings().bank_opt_in_url
    context = {"msisdn": msisdn, "opt_in_url": opt_in_url}

    if opt_in_url is None:
        context["error"] = (
            "Cannot send the request: BANK_BASE_URL and BANK_OPT_IN_PATH "
            "must be set in .env."
        )
        return templates.TemplateResponse(request, "register.html", context, status_code=503)
    if not is_valid_msisdn(msisdn):
        context["error"] = "The MSISDN must contain exactly 9 digits."
        return templates.TemplateResponse(request, "register.html", context, status_code=400)
    customer = db.scalar(select(Customer).where(Customer.msisdn == msisdn))
    if customer is None:
        context["error"] = f"No customer with MSISDN {msisdn}. Create it first."
        return templates.TemplateResponse(request, "register.html", context, status_code=404)

    call = send_opt_in(db, opt_in_url, msisdn)
    context["call"] = call
    context["opted_in"] = is_opt_in_success(call)
    if context["opted_in"] and not customer.opted_in:
        customer.opted_in = True
        customer.opted_in_at = datetime.now(timezone.utc)
        db.commit()
    return templates.TemplateResponse(request, "register.html", context)


@router.get("/messages", response_class=HTMLResponse)
def message_list(request: Request, msisdn: str = "", db: Session = Depends(get_db)):
    msisdn = msisdn.strip()
    context: dict = {"msisdn": msisdn, "messages": None}
    if msisdn:
        if not is_valid_msisdn(msisdn):
            context["error"] = "The MSISDN must contain exactly 9 digits."
        else:
            context["messages"] = db.scalars(
                select(MessageSent)
                .where(MessageSent.msisdn == msisdn)
                .order_by(MessageSent.created_at.desc(), MessageSent.id.desc())
            ).all()
    return templates.TemplateResponse(request, "messages.html", context)


def _format_form_error(error: dict) -> str:
    field = str(error["loc"][0]) if error.get("loc") else ""
    label = FIELD_LABELS.get(field, field)
    if field == "msisdn":
        return f"{label}: must contain exactly 9 digits."
    if error["type"] in ("missing", "string_too_short"):
        return f"{label}: required field."
    if field == "dob":
        return f"{label}: invalid date."
    return f"{label}: {error['msg']}"
