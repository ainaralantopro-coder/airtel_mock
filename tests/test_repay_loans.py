import copy
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

import deposit_all
import generate_customers
import repay_loans
from app.config import get_settings
from app.database import SessionLocal
from app.models import Customer
from tests.test_repay import BORROWER, LOOKUP, bank, balance, repayments  # noqa: F401  (bank est une fixture)


def msisdns() -> list[str]:
    with SessionLocal() as db:
        return sorted(db.scalars(select(Customer.msisdn)))


@pytest.mark.parametrize("workers", ["1", "3"])
def test_repays_whole_outstanding_for_each_msisdn(client, bank, workers, capsys):  # noqa: F811
    assert generate_customers.main(["3"]) == 0
    assert deposit_all.main(["20000"]) == 0
    assert repay_loans.main([*msisdns(), "--workers", workers]) == 0

    repaid = [payload for path, payload in bank["requests"] if path == "/repay-loan"]
    assert sorted(p["msisdn"] for p in repaid) == msisdns()
    assert {(p["repaymentAmount"], p["loanId"]) for p in repaid} == {(16800, "LN0001")}  # encours total
    assert {r.status for r in repayments()} == {"SUCCESS"}
    assert {balance(m) for m in msisdns()} == {Decimal("3200")}  # 20 000 - 16 800
    assert "SUCCESS    3" in capsys.readouterr().out


def test_each_loan_has_its_own_amount(client, customer, bank, capsys):  # noqa: F811
    lookup = copy.deepcopy(LOOKUP)
    lookup["data"]["additionalInfo"]["loanInfo"][1]["totalAmountOutstanding"] = "7300"
    bank["responses"]["/lookup-details"] = (200, lookup)
    deposit_all.main(["10000"])
    assert repay_loans.main([BORROWER]) == 0
    [(_, payload)] = [r for r in bank["requests"] if r[0] == "/repay-loan"]
    assert payload["repaymentAmount"] == 7300 and balance(BORROWER) == Decimal("2700")


@pytest.mark.parametrize(
    "outstanding, deposit, message",
    [
        ("16800", "100", "Insufficient balance"),
        ("3508.91", "10000", "has decimals"),
    ],
)
def test_skipped_without_bank_call(client, customer, bank, outstanding, deposit, message, capsys):  # noqa: F811
    lookup = copy.deepcopy(LOOKUP)
    lookup["data"]["additionalInfo"]["loanInfo"][1]["totalAmountOutstanding"] = outstanding
    bank["responses"]["/lookup-details"] = (200, lookup)
    deposit_all.main([deposit])
    assert repay_loans.main([BORROWER]) == 0

    out = capsys.readouterr().out
    assert "SKIPPED" in out and message in out
    assert [path for path, _ in bank["requests"]] == ["/lookup-details"]
    assert repayments() == [] and balance(BORROWER) == Decimal(deposit)


def test_lookup_ko_and_bank_down(client, customer, bank, capsys):  # noqa: F811
    deposit_all.main(["20000"])
    bank["responses"]["/repay-loan"] = httpx.ConnectError("refused")
    assert repay_loans.main([BORROWER]) == 0
    bank["responses"]["/lookup-details"] = (200, {"responseCode": "404", "message": "No loan"})
    assert repay_loans.main([BORROWER]) == 0

    out = capsys.readouterr().out
    assert "UNKNOWN" in out and "refused" in out  # pas de réponse : rien n'est débité
    assert "LOOKUP_KO" in out and "No loan" in out
    assert [r.status for r in repayments()] == ["UNKNOWN"] and balance(BORROWER) == Decimal("20000")


def test_not_configured(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "bank_repay_loan_path", "")
    assert repay_loans.main(["331000001"]) == 1


@pytest.mark.parametrize("argv", [[], ["--workers", "0", "331000001"], ["x" * 51]])
def test_invalid_arguments(client, argv):
    with pytest.raises(SystemExit):
        repay_loans.main(argv)
