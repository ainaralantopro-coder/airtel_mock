"""Opérations sur le wallet Mobile Money des clients.

Tout mouvement passe par deposit() ou withdraw() : le solde du client et l'historique (WalletTransaction)
sont mis à jour dans la même transaction, la ligne du client étant verrouillée (SELECT ... FOR UPDATE)
pour que deux opérations simultanées ne faussent pas le solde.
"""

import secrets
from datetime import datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Customer, WalletTransaction
from app.web import format_ariary

MAX_DESCRIPTION_LENGTH = 255
MAX_AMOUNT = Decimal(10) ** 15  # bien en deçà de Numeric(18, 2)


class WalletError(Exception):
    """Opération refusée (montant invalide, solde insuffisant) : rien n'a été modifié."""


class InsufficientBalance(WalletError):
    """Débit supérieur au solde disponible."""


def parse_amount(text: str) -> Decimal:
    """Montant saisi en Ariary : nombre entier strictement positif."""
    try:
        amount = Decimal(text.strip())
    except InvalidOperation:
        raise WalletError("The amount must be a whole number greater than 0.") from None
    if not amount.is_finite() or amount <= 0 or amount != amount.to_integral_value():
        raise WalletError("The amount must be a whole number greater than 0.")
    if amount >= MAX_AMOUNT:
        raise WalletError(f"The amount must be less than {format_ariary(MAX_AMOUNT)}.")
    return amount.quantize(Decimal(1))


def deposit(db: Session, customer_id: int, amount: Decimal, description: str | None = None) -> WalletTransaction:
    return _move(db, customer_id, WalletTransaction.TYPE_DEPOSIT, amount, description)


def withdraw(db: Session, customer_id: int, amount: Decimal, description: str | None = None) -> WalletTransaction:
    return _move(db, customer_id, WalletTransaction.TYPE_WITHDRAWAL, amount, description)


def autostrike(db: Session, customer_id: int, amount: Decimal, description: str | None = None) -> WalletTransaction:
    """Prélèvement de recouvrement demandé par la banque. Ne valide pas la transaction : l'appelant enregistre
    sa propre trace puis fait db.commit(), pour que débit et trace soient écrits ensemble."""
    return _move(db, customer_id, WalletTransaction.TYPE_AUTOSTRIKE, amount, description, commit=False)


def _move(
    db: Session, customer_id: int, type_: str, amount: Decimal, description: str | None, commit: bool = True
) -> WalletTransaction:
    if amount <= 0:
        raise WalletError("The amount must be greater than 0.")
    if amount >= MAX_AMOUNT:
        raise WalletError(f"The amount must be less than {format_ariary(MAX_AMOUNT)}.")
    description = (description or "").strip() or None
    if description and len(description) > MAX_DESCRIPTION_LENGTH:
        raise WalletError(f"The description is limited to {MAX_DESCRIPTION_LENGTH} characters.")

    # populate_existing : relit le solde verrouillé même si le client est déjà chargé dans la session
    customer = db.scalar(
        select(Customer)
        .where(Customer.id == customer_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if customer is None:
        raise WalletError("Customer not found.")

    if type_ == WalletTransaction.TYPE_DEPOSIT:
        balance = customer.balance + amount
    else:
        balance = customer.balance - amount
        if balance < 0:
            db.rollback()  # libère le verrou
            raise InsufficientBalance(
                f"Insufficient balance: {format_ariary(customer.balance)} available, "
                f"{format_ariary(amount)} requested."
            )

    customer.balance = balance
    transaction = WalletTransaction(
        customer_id=customer.id,
        msisdn=customer.msisdn,
        reference=_new_reference(),
        type=type_,
        amount=amount,
        balance_after=balance,
        description=description,
    )
    db.add(transaction)
    if commit:
        db.commit()
    else:
        db.flush()
    return transaction


def _new_reference() -> str:
    """Référence du mouvement : WLT + horodatage + 8 chiffres aléatoires."""
    return f"WLT{datetime.now():%Y%m%d%H%M%S}{secrets.randbelow(10**8):08d}"
