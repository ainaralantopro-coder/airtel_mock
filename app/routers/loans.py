"""Écrans prêt : Borrow loan (simulation USSD) et liste des prêts."""

from datetime import datetime
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bank_client import (
    is_success,
    new_transaction_id,
    response_json,
    send_apply_loan,
    send_check_eligibility,
)
from app.config import get_settings
from app.database import get_db
from app.models import CallSent, Loan
from app.schemas import is_valid_msisdn
from app.web import templates

router = APIRouter(prefix="/loans", include_in_schema=False)

INVALID_MSISDN = "The MSISDN must contain exactly 9 digits."
DUE_DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
)


def _borrow_context(msisdn: str = "", **extra) -> dict:
    settings = get_settings()
    return {
        "msisdn": msisdn,
        "eligibility_url": settings.bank_check_eligibility_url,
        "apply_url": settings.bank_apply_loan_url,
        **extra,
    }


@router.get("/borrow", response_class=HTMLResponse)
def borrow_form(request: Request, msisdn: str = ""):
    return templates.TemplateResponse(request, "borrow.html", _borrow_context(msisdn.strip()))


@router.post("/borrow/eligibility", response_class=HTMLResponse)
async def check_eligibility(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    msisdn = str(form.get("msisdn", "")).strip()
    context = _borrow_context(msisdn)

    if context["eligibility_url"] is None or context["apply_url"] is None:
        context["error"] = (
            "BANK_BASE_URL, BANK_CHECK_ELIGIBILITY_PATH and BANK_APPLY_LOAN_PATH "
            "must be set in .env."
        )
        return templates.TemplateResponse(request, "borrow.html", context, status_code=503)
    if not is_valid_msisdn(msisdn):
        context["error"] = INVALID_MSISDN
        return templates.TemplateResponse(request, "borrow.html", context, status_code=400)

    call = send_check_eligibility(db, context["eligibility_url"], msisdn)
    body = response_json(call)
    if not is_success(body):
        context["error"] = _bank_error(call, body)
        return templates.TemplateResponse(request, "borrow.html", context)

    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    offer = {
        "min_amount": _to_decimal(data.get("minAmount")),
        "max_amount": _to_decimal(data.get("maxAmount")),
        "eligible_amount": _to_decimal(data.get("eligibleAmount")),
        "fees_amount": _to_decimal(data.get("feesAmount")),
    }
    if offer["min_amount"] is None or offer["eligible_amount"] is None:
        context["error"] = "Incomplete bank response: minAmount or eligibleAmount is missing."
    elif offer["min_amount"] > offer["eligible_amount"]:
        context["error"] = "The eligible amount is below the minimum amount: no loan possible."
    else:
        context["offer"] = offer
        context["amount"] = _plain(offer["min_amount"])
    return templates.TemplateResponse(request, "borrow.html", context)


@router.post("/borrow/apply", response_class=HTMLResponse)
async def apply_loan(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    msisdn = str(form.get("msisdn", "")).strip()
    amount_text = str(form.get("amount", "")).strip()
    offer = {
        "min_amount": _to_decimal(form.get("min_amount")),
        "max_amount": _to_decimal(form.get("max_amount")),
        "eligible_amount": _to_decimal(form.get("eligible_amount")),
        "fees_amount": _to_decimal(form.get("fees_amount")),
    }
    context = _borrow_context(msisdn, offer=offer, amount=amount_text)

    if context["apply_url"] is None:
        context.pop("offer")
        context["error"] = "BANK_APPLY_LOAN_PATH must be set in .env."
        return templates.TemplateResponse(request, "borrow.html", context, status_code=503)
    if not is_valid_msisdn(msisdn) or offer["min_amount"] is None or offer["eligible_amount"] is None:
        context.pop("offer")
        context["error"] = "Invalid request: run the eligibility check again."
        return templates.TemplateResponse(request, "borrow.html", context, status_code=400)

    amount = _to_int(amount_text)
    if amount is None or not offer["min_amount"] <= amount <= offer["eligible_amount"]:
        context["amount_error"] = (
            f"The amount must be a whole number between {_plain(offer['min_amount'])} "
            f"and {_plain(offer['eligible_amount'])}."
        )
        return templates.TemplateResponse(request, "borrow.html", context, status_code=400)

    transaction_id = new_transaction_id()
    call = send_apply_loan(db, context["apply_url"], msisdn, amount, transaction_id)
    loan = _save_loan(db, call, msisdn, transaction_id, amount, offer["fees_amount"])

    context.pop("offer")
    context["loan"] = loan
    if loan.status != Loan.STATUS_BOOKED:
        context["error"] = _bank_error(call, response_json(call))
    return templates.TemplateResponse(request, "borrow.html", context)


@router.get("", response_class=HTMLResponse)
def loan_list(request: Request, msisdn: str = "", db: Session = Depends(get_db)):
    msisdn = msisdn.strip()
    context: dict = {"msisdn": msisdn, "loans": None}
    if msisdn:
        if not is_valid_msisdn(msisdn):
            context["error"] = INVALID_MSISDN
        else:
            context["loans"] = db.scalars(
                select(Loan).where(Loan.msisdn == msisdn).order_by(Loan.created_at.desc(), Loan.id.desc())
            ).all()
    return templates.TemplateResponse(request, "loans.html", context)


def _save_loan(
    db: Session, call: CallSent, msisdn: str, transaction_id: str, amount: int, fees_amount: Decimal | None
) -> Loan:
    """Enregistre la demande, réussie (BOOKED) ou non (FAILED)."""
    body = response_json(call)
    data = body.get("data") if body and isinstance(body.get("data"), dict) else {}
    loan = Loan(
        msisdn=msisdn,
        transaction_id=transaction_id,
        requested_amount=amount,
        fees_amount=fees_amount,
        status=Loan.STATUS_BOOKED if is_success(body) else Loan.STATUS_FAILED,
        response_code=_to_str(body.get("responseCode")) if body else None,
        response_message=_bank_error(call, body) if not is_success(body) else _to_str(body.get("message")),
        loan_id=_to_str(data.get("loanId")),
        loan_amount=_to_decimal(data.get("loanAmount")),
        loan_fees=_to_decimal(data.get("loanfees")),
        outstanding_amount=_to_decimal(data.get("outstandingAmount")),
        due_date=_to_datetime(data.get("dueDate")),
        tenure_id=_to_str(data.get("tenureId")),
        tenure_name=_to_str(data.get("tenureName")),
        interest_rate=_to_str(data.get("interestRate")),
        is_disbursed=False,
    )
    db.add(loan)
    db.commit()
    return loan


def _bank_error(call: CallSent, body: dict | None) -> str:
    if call.error:
        return f"Bank call failed: {call.error}"
    if body is None:
        return f"Unreadable bank response (HTTP {call.response_status})."
    return str(body.get("message") or f"Bank response: responseCode {body.get('responseCode')}")


def _to_decimal(value) -> Decimal | None:
    if value is None or isinstance(value, bool) or str(value).strip() == "":
        return None
    try:
        result = Decimal(str(value).strip())
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


def _to_int(value: str) -> int | None:
    number = _to_decimal(value)
    if number is None or number != number.to_integral_value():
        return None
    return int(number)


def _to_str(value) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    return str(value).strip()


def _to_datetime(value) -> datetime | None:
    text = _to_str(value)
    if text is None:
        return None
    for fmt in DUE_DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _plain(amount: Decimal) -> str:
    """Montant sans séparateurs, pour les champs de formulaire."""
    return f"{amount:f}" if amount != amount.to_integral_value() else str(int(amount))
