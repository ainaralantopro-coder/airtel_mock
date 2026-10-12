"""Génère des clients aléatoires dans la base du mock Airtel.

    .venv/bin/python generate_customers.py 50                # 50 clients, MSISDN 33xxxxxxx
    .venv/bin/python generate_customers.py 50 --prefix 34    # MSISDN 34xxxxxxx
    .venv/bin/python generate_customers.py 50 --seed 42      # tirage reproductible

MSISDN sur 9 chiffres (ex. 331245678) et numéro d'identité sur 11 chiffres (ex. 10234578990), tous deux
uniques, y compris par rapport aux clients déjà en base. Champs fixes : nationality MG, grade SUBS,
account_status N, registration_status SUBS, PIN défini, compte non barré.
"""

import argparse
import os
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)  # .env est relatif au dossier du projet
sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import Customer  # noqa: E402
from app.schemas import CustomerCreate  # noqa: E402

MSISDN_LENGTH = 9
ID_NUMBER_LENGTH = 11
MIN_AGE, MAX_AGE = 18, 70

FIRST_NAMES = (
    "Andry", "Hery", "Fanja", "Lalao", "Mamy", "Nirina", "Rivo", "Tiana", "Voahangy", "Zo", "Haingo", "Tojo",
    "Fara", "Miora", "Njaka", "Onja", "Sitraka", "Toky", "Volana", "Mialy", "Feno", "Lova", "Ny Aina", "Hasina",
    "Rindra", "Tsiry", "Koloina", "Fitiavana", "Domoina", "Herizo", "Jean", "Marie", "Paul", "Claudine",
)
LAST_NAMES = (
    "Rakoto", "Rabe", "Rasoa", "Randria", "Rakotomalala", "Andrianarisoa", "Rasoanaivo", "Razafindrakoto",
    "Ravelonarivo", "Randrianasolo", "Rajaonarison", "Ramanantsoa", "Rakotondrabe", "Razanamparany",
    "Andriamihaja", "Rasolofonirina", "Ratsimbazafy", "Raharison", "Rafanomezantsoa", "Rakotoarisoa",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create random customers in the Airtel mock database.")
    parser.add_argument("count", type=int, help="number of customers to create")
    parser.add_argument("--prefix", default="33", help="MSISDN prefix (default: 33)")
    parser.add_argument("--seed", type=int, help="random seed, to get the same customers again")
    args = parser.parse_args(argv)

    if args.count < 1:
        parser.error("count must be at least 1")
    if not args.prefix.isdigit() or len(args.prefix) >= MSISDN_LENGTH:
        parser.error(f"prefix must be digits only, shorter than {MSISDN_LENGTH}")

    rng = random.Random(args.seed)
    with SessionLocal() as db:
        used_msisdns = set(db.scalars(select(Customer.msisdn)))
        used_ids = set(db.scalars(select(Customer.id_number)))

        free = 10 ** (MSISDN_LENGTH - len(args.prefix)) - sum(m.startswith(args.prefix) for m in used_msisdns)
        if args.count > free:
            print(f"Only {free} MSISDN left with prefix {args.prefix}: ask for fewer customers or another prefix.")
            return 1

        customers = []
        for _ in range(args.count):
            msisdn = _unique(used_msisdns, lambda: args.prefix + _digits(rng, MSISDN_LENGTH - len(args.prefix)))
            id_number = _unique(used_ids, lambda: str(rng.randint(1, 9)) + _digits(rng, ID_NUMBER_LENGTH - 1))
            customer_in = CustomerCreate(
                msisdn=msisdn,
                first_name=rng.choice(FIRST_NAMES),
                last_name=rng.choice(LAST_NAMES),
                dob=_random_dob(rng),
                id_number=id_number,
                nationality="MG",
                grade="SUBS",
                account_status="N",
                registration_status="SUBS",
                is_pin_set=True,
                is_barred=False,
            )
            payload = customer_in.model_dump()
            payload["dob"] = datetime.combine(payload["dob"], datetime.min.time())
            customers.append(Customer(**payload))

        db.add_all(customers)
        db.commit()

    print(f"{len(customers)} customer(s) created:")
    for c in customers:
        print(f"  {c.msisdn}  {c.first_name} {c.last_name}  {c.dob:%Y-%m-%d}  {c.id_number}")
    return 0


def _digits(rng: random.Random, length: int) -> str:
    return "".join(rng.choice("0123456789") for _ in range(length))


def _unique(used: set[str], make) -> str:
    while True:
        value = make()
        if value not in used:
            used.add(value)
            return value


def _random_dob(rng: random.Random) -> date:
    today = date.today()
    day = min(today.day, 28)  # évite le 29 février
    youngest = date(today.year - MIN_AGE, today.month, day)
    oldest = date(today.year - MAX_AGE, today.month, day)
    return oldest + timedelta(days=rng.randint(0, (youngest - oldest).days))


if __name__ == "__main__":
    sys.exit(main())
