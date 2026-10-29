"""Autostrike : la banque prélève sur le wallet de l'emprunteur pour recouvrer un prêt en défaut.

Le wallet de subscriber.msisdn est débité du montant demandé, sans prélèvement partiel. La transaction est en
échec (TF), sans rien prélever, si le client n'est pas en défaut, si le montant dépasse son montant en défaut
ou si son solde ne suffit pas. Un prélèvement réussi diminue d'autant le montant en défaut.

Chaque demande est enregistrée dans la table autostrike ; un même transaction.id renvoyé (retry) reçoit la même
réponse, sans nouveau débit.
"""

import secrets
import string

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import wallet
from app.config import get_settings
from app.database import get_db
from app.errors import ApiError, autostrike_status
from app.models import Autostrike, Customer, LoanDefaulter
from app.schemas import AutostrikeRequest
from app.security import require_token
from app.web import format_amount

router = APIRouter(prefix="/standard/v1/loan", tags=["autostrike"], dependencies=[Depends(require_token)])

_ID_ALPHABET = string.ascii_uppercase + string.digits


@router.post("/autostrike")
def autostrike(body: AutostrikeRequest, db: Session = Depends(get_db)):
    msisdn = body.subscriber.msisdn
    transaction_id = body.transaction.id
    amount = body.transaction.amount

    previous = _find(db, transaction_id)
    if previous is not None:
        return _replay(previous, msisdn, amount)

    customer = db.scalar(select(Customer).where(Customer.msisdn == msisdn))
    if customer is None:
        raise ApiError(404, "Subscriber not found")

    record = Autostrike(
        transaction_id=transaction_id,
        reference=body.reference,
        customer_id=customer.id,
        msisdn=msisdn,
        amount=amount,
        currency=body.transaction.currency or body.subscriber.currency,
        country=body.transaction.country or body.subscriber.country,
        reference_id=_new_reference_id(db),
        airtel_money_id=f"AUTOSTRK-{''.join(secrets.choice(_ID_ALPHABET) for _ in range(10))}-{transaction_id}",
    )
    transfer = f"transfer of {' '.join(filter(None, [record.currency, format_amount(amount)]))}"
    parties = f"between the sender: {msisdn} and receiver: {get_settings().autostrike_collection_msisdn}"

    def fail(reason: str) -> None:
        record.status = Autostrike.STATUS_FAILED
        record.message = f"{transfer} failed {parties}: {reason}. Txn ID: {record.reference_id}."

    defaulter = _active_defaulter(db, msisdn)
    if defaulter is None:
        fail("subscriber is not a defaulter")
    elif amount > defaulter.amount:
        fail(f"amount exceeds the defaulted amount of {format_amount(defaulter.amount)}")
    else:
        try:
            movement = wallet.autostrike(
                db, customer.id, amount, f"Autostrike {transaction_id} (ref {body.reference})"
            )
        except wallet.InsufficientBalance:
            fail("insufficient balance")
        except wallet.WalletError as exc:
            raise ApiError(400, f"Invalid request: {exc}") from None
        else:
            # La dette restante diminue ; le statut defaulter n'est levé que par la banque (Set defaulter status)
            defaulter.amount -= amount
            record.status = Autostrike.STATUS_SUCCESS
            record.wallet_transaction_id = movement.id
            record.message = f"{transfer} completed successfully {parties}. Txn ID: {record.reference_id}."

    db.add(record)
    try:
        db.commit()
    except IntegrityError:
        # Même transaction.id traité en parallèle : notre débit est annulé, on renvoie le résultat de l'autre
        db.rollback()
        previous = _find(db, transaction_id)
        if previous is None:
            raise
        return _replay(previous, msisdn, amount)
    return _response(record)


def _active_defaulter(db: Session, msisdn: str) -> LoanDefaulter | None:
    """Prêt en défaut du client (un seul prêt actif à la fois), verrouillé jusqu'à la fin de la transaction :
    deux autostrikes simultanés ne peuvent pas prélever plus que le montant en défaut."""
    return db.scalar(
        select(LoanDefaulter)
        .where(LoanDefaulter.msisdn == msisdn, LoanDefaulter.is_defaulter.is_(True))
        .order_by(LoanDefaulter.id)
        .limit(1)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _find(db: Session, transaction_id: str) -> Autostrike | None:
    return db.scalar(select(Autostrike).where(Autostrike.transaction_id == transaction_id))


def _replay(previous: Autostrike, msisdn: str, amount) -> dict:
    if previous.msisdn != msisdn or previous.amount != amount:
        raise ApiError(409, f"transaction.id {previous.transaction_id} was already used for another autostrike")
    return _response(previous)


def _response(record: Autostrike) -> dict:
    return {
        "data": {
            "transaction": {
                "reference_id": record.reference_id,
                "airtel_money_id": record.airtel_money_id,
                "id": record.transaction_id,
                "status": record.status,
            }
        },
        "status": autostrike_status(200, record.message, success=record.status == Autostrike.STATUS_SUCCESS),
    }


def _new_reference_id(db: Session) -> str:
    """Txn ID Airtel sur 11 chiffres (ex. 10000460329), unique."""
    while True:
        reference_id = f"1{secrets.randbelow(10**10):010d}"
        if db.scalar(select(Autostrike.id).where(Autostrike.reference_id == reference_id)) is None:
            return reference_id
