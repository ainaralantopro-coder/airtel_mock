"""Remboursement total (Repay loan) en série pour une liste de MSISDN, pour charger la couche d'intégration de la banque.

    .venv/bin/python repay_loans.py 331245678 331245679              # MSISDN en arguments
    .venv/bin/python repay_loans.py --file msisdns.txt --workers 20  # un MSISDN par ligne (# = commentaire)

Pour chaque MSISDN, même enchaînement que l'écran Repay loan : Lookup Details, puis Repay Loan de la totalité de
l'encours (totalAmountOutstanding), chaque prêt ayant son propre montant. L'emprunteur paie lui-même : son wallet
doit couvrir l'encours (voir deposit_all.py) et n'est débité que si la banque confirme. Les remboursements sont
enregistrés comme depuis l'écran (historique de Repay loan) et chaque appel est journalisé dans call_sent.
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

from sqlalchemy import create_engine  # noqa: E402

from app.bank_client import is_success, response_json, send_lookup_details  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import LoanRepayment  # noqa: E402
from app.routers.loans import _bank_error  # noqa: E402
from app.routers.repay import _check_amount, _parse_lookup, lock_payer, send_repayment  # noqa: E402
from app.schemas import MSISDN_MAX_LENGTH, is_valid_msisdn  # noqa: E402
from app.web import format_amount, format_ariary  # noqa: E402

# Résultats possibles en plus des statuts de LoanRepayment (SUCCESS, FAILED, UNKNOWN)
LOOKUP_KO = "LOOKUP_KO"  # Lookup Details en échec ou sans prêt à rembourser : pas de Repay Loan
SKIPPED = "SKIPPED"  # remboursement impossible côté mock (solde insuffisant, montant hors bornes...)
ERROR = "ERROR"  # erreur côté script (base de données...)


@dataclass
class Result:
    msisdn: str
    outcome: str
    message: str = ""
    amount: str | None = None
    loan_id: str | None = None
    lookup_ms: int | None = None
    repay_ms: int | None = None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Repay the whole outstanding loan of each MSISDN (Lookup + Repay).")
    parser.add_argument("msisdns", nargs="*", help="MSISDN list")
    parser.add_argument("--file", type=Path, help="file with one MSISDN per line (# starts a comment)")
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
    if args.workers < 1:
        parser.error("workers must be at least 1")

    settings = get_settings()
    lookup_url, repay_url = settings.bank_lookup_details_url, settings.bank_repay_loan_url
    if lookup_url is None or repay_url is None:
        print("BANK_REPAYMENT_BASE_URL (or BANK_BASE_URL), BANK_LOOKUP_DETAILS_PATH and BANK_REPAY_LOAN_PATH "
              "must be set in .env.")
        return 1

    # Chaque demande garde une connexion (payeur verrouillé) et en ouvre une seconde pour le journal :
    # le pool par défaut (15 connexions) se bloquerait au-delà de 7 demandes simultanées.
    engine = create_engine(settings.database_url, pool_pre_ping=True, pool_size=2 * args.workers, max_overflow=0)
    default_bind = SessionLocal.kw["bind"]
    SessionLocal.configure(bind=engine)
    try:
        print(f"{len(msisdns)} repayment(s), {args.workers} at a time, to {repay_url}")
        started = time.perf_counter()
        results = []
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(repay, m, lookup_url, repay_url) for m in msisdns]
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                print(_line(result), flush=True)
        _summary(results, time.perf_counter() - started)
    finally:
        SessionLocal.configure(bind=default_bind)
        engine.dispose()
    return 0


def repay(msisdn: str, lookup_url: str, repay_url: str) -> Result:
    try:
        with SessionLocal() as db:
            call = send_lookup_details(db, lookup_url, msisdn)
            body = response_json(call)
            result = Result(msisdn, LOOKUP_KO, lookup_ms=call.duration_ms)
            if not is_success(body):
                result.message = _bank_error(call, body)
                return result
            offer, error = _parse_lookup(body)
            if error:
                result.message = error
                return result

            outstanding = offer["amount_outstanding"]
            result.outcome, result.loan_id, result.amount = SKIPPED, offer["loan_id"], format_amount(outstanding)
            if outstanding <= 0:
                result.message = "Nothing to repay: the outstanding amount is 0."
                return result
            if outstanding != outstanding.to_integral_value():
                result.message = "The outstanding amount has decimals: repayments are whole numbers."
                return result
            amount = int(outstanding)
            error = _check_amount(amount, offer)
            if error:
                result.message = error
                return result

            payer = lock_payer(db, msisdn)
            if payer is None:
                result.message = f"No customer with MSISDN {msisdn} in the mock: nobody to debit."
                return result
            if payer.balance < amount:
                db.rollback()  # libère le verrou
                result.message = (f"Insufficient balance: {format_ariary(payer.balance)} available, "
                                  f"{format_ariary(amount)} requested.")
                return result

            started = time.perf_counter()  # appel Repay Loan, débit et enregistrement compris
            repayment = send_repayment(db, repay_url, msisdn, payer, amount, offer["loan_id"])
            result.repay_ms = int((time.perf_counter() - started) * 1000)
            result.outcome, result.message = repayment.status, repayment.response_message or ""
            return result
    except Exception as exc:  # une erreur sur un MSISDN ne doit pas arrêter les autres
        return Result(msisdn, ERROR, message=f"{type(exc).__name__}: {exc}")


def _line(r: Result) -> str:
    timings = " ".join(f"{name} {ms} ms" for name, ms in (("lookup", r.lookup_ms), ("repay", r.repay_ms))
                       if ms is not None)
    details = " ".join(filter(None, [
        f"amount {r.amount}" if r.amount is not None else "", f"loanId {r.loan_id}" if r.loan_id else "",
        timings, r.message,
    ]))
    return f"  {r.msisdn:<15} {r.outcome:<10} {details}"


def _summary(results: list[Result], elapsed: float) -> None:
    print(f"\n{len(results)} request(s) in {elapsed:.1f} s ({len(results) / elapsed:.1f} per second)")
    outcomes = (LoanRepayment.STATUS_SUCCESS, LoanRepayment.STATUS_FAILED, LoanRepayment.STATUS_UNKNOWN,
                LOOKUP_KO, SKIPPED, ERROR)
    for outcome in outcomes:
        count = sum(r.outcome == outcome for r in results)
        if count:
            print(f"  {outcome:<10} {count}")
    for name in ("lookup_ms", "repay_ms"):
        durations = sorted(getattr(r, name) for r in results if getattr(r, name) is not None)
        if durations:
            p95 = durations[min(len(durations) - 1, int(len(durations) * 0.95))]
            print(f"  {name[:-3]:<10} avg {statistics.mean(durations):.0f} ms, p95 {p95} ms, max {durations[-1]} ms")


if __name__ == "__main__":
    sys.exit(main())
