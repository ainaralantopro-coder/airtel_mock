"""Écrans prêt : Borrow loan (simulation USSD), liste des prêts, statut (Apply Loan Status) et décaissement
(Confirm / Cancel Loan)."""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bank_client import (
    is_pending,
    is_success,
    new_transaction_id,
    response_json,
    send_apply_loan,
    send_apply_loan_status,
    send_check_eligibility,
    send_cancel_loan,
    send_confirm_loan,
)
from app.config import get_settings
from app.database import get_db
from app.models import CallSent, Loan
from app.schemas import MSISDN_MAX_LENGTH, is_valid_msisdn
from app.web import templates

router = APIRouter(prefix="/loans", include_in_schema=False)

INVALID_MSISDN = f"The MSISDN is required ({MSISDN_MAX_LENGTH} characters max)."
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
    context = _list_context(db, msisdn)
    if msisdn and not is_valid_msisdn(msisdn):
        context["error"] = INVALID_MSISDN
    return templates.TemplateResponse(request, "loans.html", context)


@router.post("/{loan_pk}/status", response_class=HTMLResponse)
def check_loan_status(request: Request, loan_pk: int, db: Session = Depends(get_db)):
    """Timeout : la réponse d'Apply Loan est considérée comme perdue, on demande son issue à la banque
    (Apply Loan Status) et on met le prêt à jour en conséquence."""
    status_url = get_settings().bank_apply_loan_status_url
    loan = db.get(Loan, loan_pk)
    error, status_code = None, 400
    if loan is None:
        error, status_code = "Loan not found.", 404
    elif status_url is None:
        error, status_code = "BANK_APPLY_LOAN_STATUS_PATH must be set in .env.", 503
    elif not loan.status_checkable:
        error = f"The status of a {loan.status} loan cannot be checked."
    if error:
        context = _list_context(db, loan.msisdn if loan else "")
        context["error"] = error
        return templates.TemplateResponse(request, "loans.html", context, status_code=status_code)

    call = send_apply_loan_status(db, status_url, loan.msisdn, loan.transaction_id)
    body = response_json(call)
    context = _list_context(db, loan.msisdn)
    if body is None:
        # Pas de réponse exploitable : l'issue reste inconnue, le prêt n'est pas modifié
        context["error"] = _bank_error(call, body) + " Loan unchanged."
        return templates.TemplateResponse(request, "loans.html", context)

    loan.response_code = _to_str(body.get("responseCode"))
    loan.response_message = _bank_error(call, body) if not is_success(body) else _to_str(body.get("message"))
    if is_success(body):
        loan.status = Loan.STATUS_BOOKED
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        for field, value in _loan_fields(data).items():
            if value is not None:
                setattr(loan, field, value)
    elif is_pending(body):
        loan.status = Loan.STATUS_PENDING
    else:
        loan.status = Loan.STATUS_FAILED
    db.commit()

    level = {Loan.STATUS_BOOKED: "success", Loan.STATUS_PENDING: "warning"}.get(loan.status, "error")
    context[level] = f"Apply Loan Status for {loan.transaction_id}: {loan.status}. {loan.response_message or ''}"
    return templates.TemplateResponse(request, "loans.html", context)


@router.post("/{loan_pk}/disburse", response_class=HTMLResponse)
def disburse_loan(request: Request, loan_pk: int, db: Session = Depends(get_db)):
    """Disburse OK : confirme à la banque que le prêt a été versé sur le compte Mobile Money (Confirm Loan)."""
    confirm_url = get_settings().bank_confirm_loan_url
    loan, refusal = _pending_loan(request, db, loan_pk, confirm_url, "BANK_CONFIRM_LOAN_PATH")
    if refusal:
        return refusal

    external_transaction_id = new_transaction_id()
    amount = loan.loan_amount if loan.loan_amount is not None else Decimal(loan.requested_amount)
    call = send_confirm_loan(
        db, confirm_url, loan.msisdn, loan.transaction_id, int(amount), loan.loan_id, external_transaction_id
    )
    body = response_json(call)
    context = _list_context(db, loan.msisdn)
    if is_success(body):
        loan.is_disbursed = True
        loan.external_transaction_id = external_transaction_id
        loan.disbursed_at = datetime.now(timezone.utc)
        db.commit()
        context["success"] = str(body.get("message") or "Loan confirmed.") + f" ({loan.transaction_id})"
    else:
        context["error"] = _bank_error(call, body)
    return templates.TemplateResponse(request, "loans.html", context)


@router.post("/{loan_pk}/cancel", response_class=HTMLResponse)
def cancel_loan(request: Request, loan_pk: int, db: Session = Depends(get_db)):
    """Disburse KO : le versement a échoué, on demande à la banque d'annuler le prêt (Cancel Loan)."""
    cancel_url = get_settings().bank_cancel_loan_url
    loan, refusal = _pending_loan(request, db, loan_pk, cancel_url, "BANK_CANCEL_LOAN_PATH")
    if refusal:
        return refusal

    call = send_cancel_loan(db, cancel_url, loan.msisdn, loan.transaction_id)
    body = response_json(call)
    context = _list_context(db, loan.msisdn)
    if is_success(body):
        loan.status = Loan.STATUS_CANCELLED
        loan.cancelled_at = datetime.now(timezone.utc)
        db.commit()
        context["success"] = str(body.get("message") or "Loan cancelled.") + f" ({loan.transaction_id})"
    else:
        context["error"] = _bank_error(call, body)
    return templates.TemplateResponse(request, "loans.html", context)


def _pending_loan(
    request: Request, db: Session, loan_pk: int, bank_url: str | None, path_setting: str
) -> tuple[Loan | None, HTMLResponse | None]:
    """Prêt accordé en attente de décaissement, ou la page d'erreur à renvoyer à la place."""
    loan = db.get(Loan, loan_pk)
    error, status_code = None, 400
    if loan is None:
        error, status_code = "Loan not found.", 404
    elif bank_url is None:
        error, status_code = f"{path_setting} must be set in .env.", 503
    elif loan.is_disbursed:
        error = "This loan is already disbursed."
    elif not loan.awaiting_disbursement:
        error = "Only a booked loan can be disbursed or cancelled."
    if error is None:
        return loan, None

    context = _list_context(db, loan.msisdn if loan else "")
    context["error"] = error
    return loan, templates.TemplateResponse(request, "loans.html", context, status_code=status_code)


def _list_context(db: Session, msisdn: str) -> dict:
    loans = None
    if msisdn and is_valid_msisdn(msisdn):
        loans = db.scalars(
            select(Loan).where(Loan.msisdn == msisdn).order_by(Loan.created_at.desc(), Loan.id.desc())
        ).all()
    settings = get_settings()
    return {
        "msisdn": msisdn,
        "loans": loans,
        "confirm_url": settings.bank_confirm_loan_url,
        "cancel_url": settings.bank_cancel_loan_url,
        "status_url": settings.bank_apply_loan_status_url,
    }


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
        is_disbursed=False,
        **_loan_fields(data),
    )
    db.add(loan)
    db.commit()
    return loan


def _loan_fields(data: dict) -> dict:
    """Colonnes de Loan tirées du "data" d'Apply Loan ou d'Apply Loan Status."""
    return {
        "loan_id": _to_str(data.get("loanId")),
        "loan_amount": _to_decimal(data.get("loanAmount")),
        "loan_fees": _to_decimal(data.get("loanfees")),
        "outstanding_amount": _to_decimal(data.get("outstandingAmount")),
        "due_date": _to_datetime(data.get("dueDate")),
        "tenure_id": _to_str(data.get("tenureId")),
        "tenure_name": _to_str(data.get("tenureName")),
        "interest_rate": _to_str(data.get("interestRate")),
    }


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
