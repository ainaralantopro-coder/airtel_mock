"""Écrans du mock : clients, enregistrement (opt-in) et messages reçus."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bank_client import is_opt_in_success, send_opt_in
from app.config import get_settings
from app.database import get_db
from app.excel import XLSX_MEDIA_TYPE, build_xlsx
from app.models import Customer, LoanDefaulter, MessageSent
from app.schemas import MSISDN_MAX_LENGTH, CustomerCreate, is_valid_msisdn
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

EXPORT_HEADERS = (
    "MSISDN", "First name", "Last name", "Date of birth", "ID number", "Nationality", "Grade",
    "Account status", "Registration status", "PIN set", "Barred", "Opted in",
)


@router.get("/", response_class=HTMLResponse)
def customer_list(request: Request, db: Session = Depends(get_db)):
    customers = db.scalars(select(Customer).order_by(Customer.created_at.desc())).all()
    return templates.TemplateResponse(request, "customers.html", {"customers": customers})


@router.get("/customers/export")
def customer_export(db: Session = Depends(get_db)):
    """Tous les clients et leurs détails, en fichier Excel."""
    customers = db.scalars(select(Customer).order_by(Customer.created_at.desc(), Customer.id.desc())).all()
    rows = (
        (
            c.msisdn, c.first_name, c.last_name, c.dob.date(), c.id_number, c.nationality, c.grade,
            c.account_status, c.registration_status, c.is_pin_set, c.is_barred, c.opted_in,
        )
        for c in customers
    )
    content = build_xlsx("Customers", EXPORT_HEADERS, rows)
    filename = f"customers_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    return Response(
        content, media_type=XLSX_MEDIA_TYPE, headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


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
        context["error"] = f"The MSISDN is required ({MSISDN_MAX_LENGTH} characters max)."
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
            context["error"] = f"The MSISDN is required ({MSISDN_MAX_LENGTH} characters max)."
        else:
            context["messages"] = db.scalars(
                select(MessageSent)
                .where(MessageSent.msisdn == msisdn)
                .order_by(MessageSent.created_at.desc(), MessageSent.id.desc())
            ).all()
    return templates.TemplateResponse(request, "messages.html", context)


@router.get("/defaulters", response_class=HTMLResponse)
def defaulter_list(request: Request, msisdn: str = "", db: Session = Depends(get_db)):
    """Statuts defaulter reçus de la banque, tous ou ceux d'un MSISDN."""
    msisdn = msisdn.strip()
    query = select(LoanDefaulter).order_by(LoanDefaulter.updated_at.desc(), LoanDefaulter.id.desc())
    if msisdn:
        query = query.where(LoanDefaulter.msisdn == msisdn)
    context = {"msisdn": msisdn, "defaulters": db.scalars(query).all()}
    return templates.TemplateResponse(request, "defaulters.html", context)


def _format_form_error(error: dict) -> str:
    field = str(error["loc"][0]) if error.get("loc") else ""
    label = FIELD_LABELS.get(field, field)
    if error["type"] in ("missing", "string_too_short"):
        return f"{label}: required field."
    if field == "dob":
        return f"{label}: invalid date."
    if error["type"] == "string_too_long":
        return f"{label}: {error['ctx']['max_length']} characters max."
    return f"{label}: {error['msg']}"
