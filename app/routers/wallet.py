"""Écran Wallet : solde d'un client, dépôt, retrait et historique des mouvements."""

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import wallet
from app.database import get_db
from app.models import Customer, WalletTransaction
from app.topup_notification import notify_if_defaulter
from app.web import templates

router = APIRouter(prefix="/wallet", include_in_schema=False)

HISTORY_LIMIT = 100


@router.get("", response_class=HTMLResponse)
def wallet_page(request: Request, msisdn: str = "", done: str = "", db: Session = Depends(get_db)):
    context = _context(db, msisdn.strip())
    if done:
        # Retour après un dépôt ou un retrait réussi (redirection : un rafraîchissement ne le rejoue pas)
        transaction = db.scalar(select(WalletTransaction).where(WalletTransaction.reference == done))
        if transaction and context["customer"] and transaction.customer_id == context["customer"].id:
            context["done"] = transaction
    status_code = 404 if context["msisdn"] and context["customer"] is None else 200
    return templates.TemplateResponse(request, "wallet.html", context, status_code=status_code)


@router.post("/deposit", response_class=HTMLResponse)
async def deposit(request: Request, db: Session = Depends(get_db)):
    return await _operate(request, db, wallet.deposit, "deposit")


@router.post("/withdraw", response_class=HTMLResponse)
async def withdraw(request: Request, db: Session = Depends(get_db)):
    return await _operate(request, db, wallet.withdraw, "withdraw")


async def _operate(request: Request, db: Session, operation, form_name: str):
    form = await request.form()
    msisdn = str(form.get("msisdn", "")).strip()
    amount_text = str(form.get("amount", "")).strip()

    context = _context(db, msisdn)
    if context["customer"] is None:
        return templates.TemplateResponse(request, "wallet.html", context, status_code=404)
    try:
        transaction = operation(db, context["customer"].id, wallet.parse_amount(amount_text))
    except wallet.WalletError as exc:
        context = _context(db, msisdn)  # solde relu après un éventuel rollback
        context[f"{form_name}_error"] = str(exc)
        context[f"{form_name}_values"] = {"amount": amount_text}
        return templates.TemplateResponse(request, "wallet.html", context, status_code=400)
    notify_if_defaulter(db, transaction)
    query = urlencode({"msisdn": msisdn, "done": transaction.reference})
    return RedirectResponse(f"/wallet?{query}", status_code=303)


def _context(db: Session, msisdn: str) -> dict:
    customer = db.scalar(select(Customer).where(Customer.msisdn == msisdn)) if msisdn else None
    transactions = []
    if customer:
        transactions = db.scalars(
            select(WalletTransaction)
            .where(WalletTransaction.customer_id == customer.id)
            .order_by(WalletTransaction.created_at.desc(), WalletTransaction.id.desc())
            .limit(HISTORY_LIMIT)
        ).all()
    return {
        "msisdn": msisdn,
        "customer": customer,
        "transactions": transactions,
        "history_limit": HISTORY_LIMIT,
        "deposit_values": {},
        "withdraw_values": {},
    }
