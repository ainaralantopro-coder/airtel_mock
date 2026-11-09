"""Fait un dépôt du même montant sur le wallet de tous les clients du mock Airtel.

    .venv/bin/python deposit_all.py 100000       # 100 000 Ar pour chaque client

Chaque dépôt suit le même chemin que l'écran Wallet : mouvement dans l'historique et, si le client est en défaut,
top-up notification envoyée à la banque. Chaque dépôt est validé séparément : relancer le script crédite à nouveau.
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)  # .env est relatif au dossier du projet
sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from app import wallet  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import Customer  # noqa: E402
from app.topup_notification import notify_if_defaulter  # noqa: E402
from app.web import format_ariary  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deposit the same amount on the wallet of every customer.")
    parser.add_argument("amount", help="amount in Ariary, whole number greater than 0")
    args = parser.parse_args(argv)

    try:
        amount = wallet.parse_amount(args.amount)
    except wallet.WalletError as exc:
        parser.error(str(exc))

    with SessionLocal() as db:
        customer_ids = db.scalars(select(Customer.id).order_by(Customer.id)).all()
        if not customer_ids:
            print("No customer in the database.")
            return 0

        for customer_id in customer_ids:
            transaction = wallet.deposit(db, customer_id, amount)
            notify_if_defaulter(db, transaction)
            notification = f"  top-up notification: {transaction.topup_notification}" if transaction.topup_notification else ""
            print(f"  {transaction.msisdn}  balance {format_ariary(transaction.balance_after)}{notification}")

    print(f"{format_ariary(amount)} deposited on {len(customer_ids)} customer(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
