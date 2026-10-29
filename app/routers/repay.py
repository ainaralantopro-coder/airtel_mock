"""Écran Repay loan : remboursement manuel d'un prêt (Lookup Details, Repay Loan, Repay Loan Status).

1. Lookup Details donne l'encours du prêt de l'emprunteur.
2. Repay Loan est envoyé au nom de l'emprunteur ; le payeur (lui-même ou un autre client) n'est connu que
   d'Airtel : son wallet est débité seulement si la banque confirme (responseCode "200"). Sa ligne est
   verrouillée pendant l'appel pour que le solde vérifié reste disponible.
3. Repay Loan Status relit l'issue d'un remboursement, utile quand la banque n'a pas répondu (UNKNOWN).
"""

from decimal import Decimal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import wallet
from app.bank_client import (
    is_success,
    new_transaction_id,
    response_json,
    send_lookup_details,
    send_repay_loan,
    send_repay_loan_status,
)
from app.config import get_settings
from app.database import SessionLocal, get_db
from app.models import CallSent, Customer, Loan, LoanRepayment
from app.routers.loans import _bank_error, _plain, _to_decimal, _to_int, _to_str
from app.web import format_amount, format_ariary, templates

router = APIRouter(prefix="/repay", include_in_schema=False)

HISTORY_LIMIT = 50
# Champs de l'offre (Lookup Details) renvoyés par le formulaire de remboursement
OFFER_FIELDS = (
    "loan_id", "principle_outstanding", "fees_outstanding", "amount_outstanding", "issued_date", "due_date",
    "min_amount", "max_amount", "total_amount",
)


@router.get("", response_class=HTMLResponse)
def repay_page(request: Request, msisdn: str = "", done: str = "", db: Session = Depends(get_db)):
    context = _context(db, msisdn.strip())
    if done:
        # Retour après un remboursement (redirection : un rafraîchissement ne le rejoue pas)
        context["done"] = db.scalar(
            select(LoanRepayment).where(
                LoanRepayment.transaction_id == done, LoanRepayment.borrower_msisdn == context["msisdn"]
            )
        )
    return templates.TemplateResponse(request, "repay.html", context)


@router.post("/lookup", response_class=HTMLResponse)
async def lookup(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    msisdn = str(form.get("msisdn", "")).strip()
    context = _context(db, msisdn)

    if context["lookup_url"] is None:
        context["error"] = "BANK_LOOKUP_DETAILS_PATH must be set in .env."
        return templates.TemplateResponse(request, "repay.html", context, status_code=503)
    if not msisdn:
        context["error"] = "The MSISDN is required."
        return templates.TemplateResponse(request, "repay.html", context, status_code=400)

    call = send_lookup_details(db, context["lookup_url"], msisdn)
    body = response_json(call)
    if not is_success(body):
        context["error"] = _bank_error(call, body)
        return templates.TemplateResponse(request, "repay.html", context)

    offer, error = _parse_lookup(body)
    if error:
        context["error"] = error
    else:
        context.update(offer=offer, payer=msisdn, amount="")
    return templates.TemplateResponse(request, "repay.html", context)


@router.post("/pay", response_class=HTMLResponse)
async def pay(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    borrower = str(form.get("msisdn", "")).strip()
    payer_msisdn = str(form.get("payer_msisdn", "")).strip()
    amount_text = str(form.get("amount", "")).strip()
    offer = _offer_from_form(form)
    context = _context(db, borrower, offer=offer, payer=payer_msisdn, amount=amount_text)

    if context["repay_url"] is None:
        context.pop("offer")
        context["error"] = "BANK_REPAY_LOAN_PATH must be set in .env."
        return templates.TemplateResponse(request, "repay.html", context, status_code=503)
    if not borrower or offer["loan_id"] is None or offer["amount_outstanding"] is None:
        context.pop("offer")
        context["error"] = "Invalid request: run the lookup again."
        return templates.TemplateResponse(request, "repay.html", context, status_code=400)

    amount = _to_int(amount_text)
    amount_error = _check_amount(amount, offer)
    if amount_error:
        context["amount_error"] = amount_error
        return templates.TemplateResponse(request, "repay.html", context, status_code=400)

    # Payeur verrouillé jusqu'à la fin : le solde vérifié ici reste disponible pour le débit
    payer = db.scalar(
        select(Customer)
        .where(Customer.msisdn == payer_msisdn)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if payer is None:
        context["payer_error"] = (
            f"No customer with MSISDN {payer_msisdn}." if payer_msisdn else "The payer MSISDN is required."
        )
        return templates.TemplateResponse(request, "repay.html", context, status_code=400)
    if payer.balance < amount:
        db.rollback()
        context["payer_error"] = (
            f"Insufficient balance: {format_ariary(payer.balance)} available, {format_ariary(amount)} requested."
        )
        return templates.TemplateResponse(request, "repay.html", context, status_code=400)

    transaction_id = new_transaction_id()
    with SessionLocal() as log_db:  # journal dans sa propre transaction : le verrou du payeur est conservé
        call = send_repay_loan(
            log_db, context["repay_url"], borrower, format_amount(Decimal(amount)), transaction_id, offer["loan_id"]
        )
        body = response_json(call)
        repayment = LoanRepayment(
            borrower_msisdn=borrower,
            payer_msisdn=payer.msisdn,
            payer_customer_id=payer.id,
            loan_ref=offer["loan_id"],
            amount=Decimal(amount),
            transaction_id=transaction_id,
        )
        _apply_response(repayment, call, body)

    if repayment.status == LoanRepayment.STATUS_SUCCESS:
        repayment.response_message = _debit_payer(db, repayment) or repayment.response_message
        _update_mock_loan(db, repayment)
    db.add(repayment)
    db.commit()
    query = urlencode({"msisdn": borrower, "done": transaction_id})
    return RedirectResponse(f"/repay?{query}", status_code=303)


@router.post("/{repayment_pk}/status", response_class=HTMLResponse)
def check_status(request: Request, repayment_pk: int, db: Session = Depends(get_db)):
    repayment = db.get(LoanRepayment, repayment_pk)
    if repayment is None:
        context = _context(db, "")
        context["error"] = "Repayment not found."
        return templates.TemplateResponse(request, "repay.html", context, status_code=404)
    status_url = get_settings().bank_repay_loan_status_url
    if status_url is None:
        context = _context(db, repayment.borrower_msisdn)
        context["error"] = "BANK_REPAY_LOAN_STATUS_PATH must be set in .env."
        return templates.TemplateResponse(request, "repay.html", context, status_code=503)

    call = send_repay_loan_status(db, status_url, repayment.borrower_msisdn, repayment.transaction_id)
    body = response_json(call)
    if body is None:
        context = _context(db, repayment.borrower_msisdn)
        context["error"] = _bank_error(call, body) + " Repayment unchanged."
        return templates.TemplateResponse(request, "repay.html", context)

    warning = None
    if is_success(body) and repayment.wallet_transaction_id is None:
        warning = _debit_payer(db, repayment)  # remboursement confirmé mais pas encore débité
    _apply_response(repayment, call, body, previous_status=repayment.status)
    if repayment.status == LoanRepayment.STATUS_SUCCESS:
        _update_mock_loan(db, repayment)
    db.commit()

    context = _context(db, repayment.borrower_msisdn)
    text = f"Repay Loan Status for {repayment.transaction_id}: {repayment.status}. {repayment.response_message or ''}"
    if warning:
        context["warning"] = f"{text} {warning}"
    else:
        context["success" if repayment.status == LoanRepayment.STATUS_SUCCESS else "error"] = text
    return templates.TemplateResponse(request, "repay.html", context)


def _parse_lookup(body: dict) -> tuple[dict | None, str | None]:
    """Offre de remboursement tirée de Lookup Details : le premier prêt ayant un encours (un seul prêt actif)."""
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    info = data.get("additionalInfo") if isinstance(data.get("additionalInfo"), dict) else {}
    raw_loans = info.get("loanInfo")
    loans = [loan for loan in raw_loans if isinstance(loan, dict)] if isinstance(raw_loans, list) else []
    if not loans:
        return None, "The bank returned no loan for this MSISDN."

    active = [loan for loan in loans if (_to_decimal(loan.get("totalAmountOutstanding")) or 0) > 0]
    chosen = (active or loans)[0]
    offer = {
        "loan_id": _to_str(chosen.get("loanId")),
        "principle_outstanding": _to_decimal(chosen.get("totalPrincipleOutstanding")),
        "fees_outstanding": _to_decimal(chosen.get("totalFeesOutstanding")),
        "amount_outstanding": _to_decimal(chosen.get("totalAmountOutstanding")),
        "issued_date": _to_str(chosen.get("issuedDate")),
        "due_date": _to_str(chosen.get("dueDate")),
        "min_amount": _to_decimal(data.get("minAmount")),
        "max_amount": _to_decimal(data.get("maxAmount")),
        "total_amount": _to_decimal(data.get("totalAmount")),
        "several_active": len(active) > 1,
    }
    if offer["loan_id"] is None or offer["amount_outstanding"] is None:
        return None, "Incomplete bank response: loanId or totalAmountOutstanding is missing."
    return offer, None


def _offer_from_form(form) -> dict:
    offer = {}
    for field in OFFER_FIELDS:
        value = form.get(field)
        offer[field] = _to_str(value) if field in ("loan_id", "issued_date", "due_date") else _to_decimal(value)
    return offer


def _check_amount(amount: int | None, offer: dict) -> str | None:
    if amount is None or amount <= 0:
        return "The amount must be a whole number greater than 0."
    if offer["min_amount"] is not None and amount < offer["min_amount"]:
        return f"The amount must be at least {format_ariary(offer['min_amount'])}."
    if offer["max_amount"] is not None and amount > offer["max_amount"]:
        return f"The amount must be at most {format_ariary(offer['max_amount'])}."
    if amount > offer["amount_outstanding"]:
        return f"The amount cannot exceed the outstanding amount of {format_ariary(offer['amount_outstanding'])}."
    return None


def _apply_response(repayment: LoanRepayment, call: CallSent, body: dict | None, previous_status: str | None = None):
    """Statut et montants d'un remboursement d'après Repay Loan ou Repay Loan Status."""
    if body is None:  # pas de réponse exploitable : issue inconnue
        repayment.status = previous_status or LoanRepayment.STATUS_UNKNOWN
        repayment.response_message = _bank_error(call, body)
        return

    repayment.response_code = _to_str(body.get("responseCode"))
    repayment.response_message = _to_str(body.get("message")) if is_success(body) else _bank_error(call, body)
    if is_success(body):
        repayment.status = LoanRepayment.STATUS_SUCCESS
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        for field, key in (("total_amt_paid", "totalAmtPaid"), ("total_outstanding_amt", "totalOutstandingAmt"),
                           ("loan_fees", "loanfees")):
            value = _to_decimal(data.get(key))
            if value is not None:
                setattr(repayment, field, value)
    elif previous_status != LoanRepayment.STATUS_SUCCESS:
        # Un remboursement déjà débité reste SUCCESS ; sinon, la banque le donne en échec
        repayment.status = LoanRepayment.STATUS_FAILED


def _debit_payer(db: Session, repayment: LoanRepayment) -> str | None:
    """Débite le payeur du remboursement confirmé ; renvoie un avertissement si ce n'est pas possible."""
    try:
        movement = wallet.repay(
            db, repayment.payer_customer_id, repayment.amount,
            f"Loan repayment {repayment.transaction_id} for {repayment.borrower_msisdn} (loan {repayment.loan_ref})",
        )
    except wallet.WalletError as exc:
        # Session annulée par le wallet : le remboursement est rechargé tel qu'en base
        if repayment in db:
            db.refresh(repayment)
        return f"Repayment confirmed by the bank, but the payer wallet was not debited: {exc}"
    repayment.wallet_transaction_id = movement.id
    return None


def _update_mock_loan(db: Session, repayment: LoanRepayment) -> None:
    """Reporte l'encours restant sur le prêt du mock correspondant, s'il existe."""
    if repayment.total_outstanding_amt is None:
        return
    loan = db.scalar(
        select(Loan).where(Loan.loan_id == repayment.loan_ref, Loan.msisdn == repayment.borrower_msisdn)
    )
    if loan is not None:
        loan.outstanding_amount = repayment.total_outstanding_amt


def _context(db: Session, msisdn: str, **extra) -> dict:
    settings = get_settings()
    repayments = []
    if msisdn:
        repayments = db.scalars(
            select(LoanRepayment)
            .where(LoanRepayment.borrower_msisdn == msisdn)
            .order_by(LoanRepayment.created_at.desc(), LoanRepayment.id.desc())
            .limit(HISTORY_LIMIT)
        ).all()
    return {
        "msisdn": msisdn,
        "lookup_url": settings.bank_lookup_details_url,
        "repay_url": settings.bank_repay_loan_url,
        "status_url": settings.bank_repay_loan_status_url,
        "repayments": repayments,
        "plain": _plain,
        **extra,
    }
