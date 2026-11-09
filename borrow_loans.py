"""Demande de prêt (Borrow loan) en série pour une liste de MSISDN, pour charger la couche d'intégration de la banque.

    .venv/bin/python borrow_loans.py 331245678 331245679              # MSISDN en arguments
    .venv/bin/python borrow_loans.py --file msisdns.txt               # un MSISDN par ligne (# = commentaire)
    .venv/bin/python borrow_loans.py --file msisdns.txt --amount 5000 --workers 20

Pour chaque MSISDN, même enchaînement que l'écran Borrow loan : Check Eligibility, puis Apply Loan avec --amount
(sans contrôle de bornes) ou, à défaut, le minAmount de l'offre. Les prêts sont enregistrés comme depuis l'écran
(visibles dans Loans, à verser avec Disburse OK / KO) et chaque appel est journalisé dans call_sent.
Les MSISDN sont traités par --workers demandes simultanées ; un MSISDN répété est envoyé autant de fois.
"""

import argparse
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)  # .env est relatif au dossier du projet
sys.path.insert(0, str(ROOT))

from app.bank_client import (  # noqa: E402
    is_success,
    new_transaction_id,
    response_json,
    send_apply_loan,
    send_check_eligibility,
)
from app.config import get_settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import Loan  # noqa: E402
from app.routers.loans import _bank_error, _save_loan, _to_decimal  # noqa: E402
from app.schemas import MSISDN_MAX_LENGTH, is_valid_msisdn  # noqa: E402

# Résultats possibles d'une demande
REFUSED = "ELIGIBILITY_KO"  # Check Eligibility en échec : pas d'Apply Loan
ERROR = "ERROR"  # erreur côté script (base de données...)


@dataclass
class Result:
    msisdn: str
    outcome: str  # BOOKED, FAILED (Apply Loan), REFUSED ou ERROR
    message: str = ""
    amount: int | None = None
    loan_id: str | None = None
    eligibility_ms: int | None = None
    apply_ms: int | None = None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Borrow a loan for each MSISDN (Check Eligibility + Apply Loan).")
    parser.add_argument("msisdns", nargs="*", help="MSISDN list")
    parser.add_argument("--file", type=Path, help="file with one MSISDN per line (# starts a comment)")
    parser.add_argument("--amount", type=int, help="amount sent to Apply Loan (default: minAmount of the offer)")
    parser.add_argument("--workers", type=int, default=10, help="simultaneous requests (default: 10)")
    args = parser.parse_args(argv)

    msisdns = list(args.msisdns)
    if args.file:
        try:
            lines = args.file.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            parser.error(f"cannot read {args.file}: {exc}")
        msisdns += [line.split("#", 1)[0].strip() for line in lines]
        msisdns = [m for m in msisdns if m]
    if not msisdns:
        parser.error("give at least one MSISDN, as argument or with --file")
    invalid = [m for m in msisdns if not is_valid_msisdn(m)]
    if invalid:
        parser.error(f"invalid MSISDN (1 to {MSISDN_MAX_LENGTH} characters): {', '.join(invalid)}")
    if args.amount is not None and args.amount <= 0:
        parser.error("amount must be greater than 0")
    if args.workers < 1:
        parser.error("workers must be at least 1")

    settings = get_settings()
    eligibility_url, apply_url = settings.bank_check_eligibility_url, settings.bank_apply_loan_url
    if eligibility_url is None or apply_url is None:
        print("BANK_ORIGINATION_BASE_URL (or BANK_BASE_URL), BANK_CHECK_ELIGIBILITY_PATH and BANK_APPLY_LOAN_PATH "
              "must be set in .env.")
        return 1

    print(f"{len(msisdns)} loan request(s), {args.workers} at a time, to {apply_url}")
    started = time.perf_counter()
    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(borrow, m, args.amount, eligibility_url, apply_url) for m in msisdns]
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            print(_line(result), flush=True)
    _summary(results, time.perf_counter() - started)
    return 0


def borrow(msisdn: str, amount: int | None, eligibility_url: str, apply_url: str) -> Result:
    try:
        with SessionLocal() as db:
            call = send_check_eligibility(db, eligibility_url, msisdn)
            body = response_json(call)
            result = Result(msisdn, REFUSED, eligibility_ms=call.duration_ms)
            if not is_success(body):
                result.message = _bank_error(call, body)
                return result

            data = body.get("data") if isinstance(body.get("data"), dict) else {}
            if amount is None:
                min_amount = _to_decimal(data.get("minAmount"))
                if min_amount is None:
                    result.message = "Incomplete bank response: minAmount is missing (use --amount)."
                    return result
                amount = int(min_amount)
            result.amount = amount

            transaction_id = new_transaction_id()
            call = send_apply_loan(db, apply_url, msisdn, amount, transaction_id)
            loan = _save_loan(db, call, msisdn, transaction_id, amount, _to_decimal(data.get("feesAmount")))
            result.outcome, result.loan_id, result.apply_ms = loan.status, loan.loan_id, call.duration_ms
            result.message = loan.response_message or ""
            return result
    except Exception as exc:  # une erreur sur un MSISDN ne doit pas arrêter les autres
        return Result(msisdn, ERROR, message=f"{type(exc).__name__}: {exc}")


def _line(r: Result) -> str:
    timings = " ".join(f"{name} {ms} ms" for name, ms in (("eligibility", r.eligibility_ms), ("apply", r.apply_ms))
                       if ms is not None)
    details = " ".join(filter(None, [
        f"amount {r.amount}" if r.amount is not None else "", f"loanId {r.loan_id}" if r.loan_id else "",
        timings, r.message,
    ]))
    return f"  {r.msisdn:<15} {r.outcome:<15} {details}"


def _summary(results: list[Result], elapsed: float) -> None:
    print(f"\n{len(results)} request(s) in {elapsed:.1f} s ({len(results) / elapsed:.1f} per second)")
    for outcome in (Loan.STATUS_BOOKED, Loan.STATUS_FAILED, REFUSED, ERROR):
        count = sum(r.outcome == outcome for r in results)
        if count:
            print(f"  {outcome:<15} {count}")
    for name in ("eligibility_ms", "apply_ms"):
        durations = sorted(getattr(r, name) for r in results if getattr(r, name) is not None)
        if durations:
            p95 = durations[min(len(durations) - 1, int(len(durations) * 0.95))]
            print(f"  {name[:-3]:<15} avg {statistics.mean(durations):.0f} ms, p95 {p95} ms, max {durations[-1]} ms")


if __name__ == "__main__":
    sys.exit(main())
