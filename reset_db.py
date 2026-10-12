"""Réinitialise les données du mock Airtel : vide toutes les tables, en gardant leur structure et les migrations.

    .venv/bin/python reset_db.py            # demande confirmation
    .venv/bin/python reset_db.py --yes      # sans confirmation
    .venv/bin/python reset_db.py --bank     # vide aussi les prêts du bank-mock (bank_mock_loans.json)

Le mock Airtel peut rester lancé. Pour --bank, le bank-mock doit être arrêté (./stop_bank.sh) : il garde
ses prêts en mémoire et réécrirait le fichier à la prochaine opération.
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)  # .env et bank_mock_loans.json sont relatifs au dossier du projet
sys.path.insert(0, str(ROOT))

from sqlalchemy import func, select, text  # noqa: E402

from app import models  # noqa: E402,F401  (enregistre les tables dans Base.metadata)
from app.database import Base, engine  # noqa: E402

BANK_PID_FILE = ROOT / "bank_mock.pid"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Empty all tables of the Airtel mock database.")
    parser.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    parser.add_argument("--bank", action="store_true", help="also empty the bank-mock loans file")
    args = parser.parse_args(argv)

    tables = Base.metadata.sorted_tables
    with engine.connect() as conn:
        counts = {table.name: conn.scalar(select(func.count()).select_from(table)) for table in tables}

    print(f"Database: {engine.url.render_as_string(hide_password=True)}")
    for name, count in counts.items():
        print(f"  {name:<14} {count} row(s)")
    if args.bank:
        if _bank_mock_running():
            print("The bank-mock is running: stop it first with ./stop_bank.sh, then run this script again.")
            return 1
        print(f"Bank-mock loans file: {_bank_loans_file()}")

    if not args.yes and input("Delete all these data? Type 'yes' to confirm: ").strip().lower() != "yes":
        print("Cancelled, nothing deleted.")
        return 1

    with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            names = ", ".join(conn.dialect.identifier_preparer.quote(table.name) for table in tables)
            conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
        else:
            for table in reversed(tables):
                conn.execute(table.delete())
    print(f"Done: {sum(counts.values())} row(s) deleted, ids restart from 1.")

    if args.bank:
        from bank_mock import store

        store.LOANS.clear()
        store.save()
        print("Bank-mock loans emptied.")
    return 0


def _bank_loans_file() -> Path:
    from bank_mock.config import get_settings

    return ROOT / get_settings().loans_file


def _bank_mock_running() -> bool:
    if os.name == "nt":  # os.kill(pid, 0) arrêterait le processus sous Windows ; start_bank.sh ne tourne pas là
        return False
    try:
        pid = int(BANK_PID_FILE.read_text().strip())
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    return True


if __name__ == "__main__":
    sys.exit(main())
