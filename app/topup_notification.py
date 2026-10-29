"""Top-up notification : prévient la banque quand le wallet d'un client en défaut est crédité.

L'envoi a lieu après l'enregistrement du dépôt : un échec de l'appel n'annule pas le dépôt, il est seulement
noté sur le mouvement (WalletTransaction.topup_notification) et journalisé dans call_sent.
À appeler après chaque crédit du wallet (aujourd'hui : les dépôts de l'écran Wallet).
"""

from datetime import timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bank_client import is_success, response_json, send_topup_notification
from app.config import get_settings
from app.models import LoanDefaulter, WalletTransaction
from app.web import format_amount


def notify_if_defaulter(db: Session, transaction: WalletTransaction) -> None:
    if transaction.type != WalletTransaction.TYPE_DEPOSIT or not _is_defaulter(db, transaction.msisdn):
        return

    url = get_settings().bank_topup_notification_url
    if url is None:
        transaction.topup_notification = WalletTransaction.NOTIFICATION_NOT_CONFIGURED
        db.commit()
        return

    db.refresh(transaction)  # created_at, fixé par la base
    call = send_topup_notification(
        db,
        url,
        msisdn=transaction.msisdn,
        top_up_amount=format_amount(transaction.amount),
        transaction_id=transaction.reference,
        transaction_timestamp=_local_timestamp(transaction),
        available_balance_amount=format_amount(transaction.balance_after),
    )
    transaction.topup_call_id = call.id
    transaction.topup_notification = (
        WalletTransaction.NOTIFICATION_SENT if is_success(response_json(call)) else WalletTransaction.NOTIFICATION_FAILED
    )
    db.commit()


def _is_defaulter(db: Session, msisdn: str) -> bool:
    active = select(LoanDefaulter.id).where(LoanDefaulter.msisdn == msisdn, LoanDefaulter.is_defaulter.is_(True))
    return db.scalar(active.limit(1)) is not None


def _local_timestamp(transaction: WalletTransaction) -> str:
    """Date du dépôt, heure locale du serveur : "2026-10-29 14:05:09"."""
    created_at = transaction.created_at
    if created_at.tzinfo is None:  # SQLite (tests) : CURRENT_TIMESTAMP est en UTC, sans fuseau
        created_at = created_at.replace(tzinfo=timezone.utc)
    return f"{created_at.astimezone():%Y-%m-%d %H:%M:%S}"
