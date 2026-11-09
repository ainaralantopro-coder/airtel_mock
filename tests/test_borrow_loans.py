import httpx
import pytest

import borrow_loans
from app.config import get_settings
from tests.test_loans import BOOKED, ELIGIBLE, bank, loans  # noqa: F401  (bank est une fixture)

MSISDNS = ["331000001", "331000002", "331000003"]


@pytest.fixture
def ok_bank(bank):  # noqa: F811
    bank["responses"] = {"/eligibility": (200, ELIGIBLE), "/apply-loan": (200, BOOKED)}
    return bank


@pytest.mark.parametrize("workers", ["1", "3"])
def test_borrows_for_each_msisdn(client, ok_bank, workers, capsys):
    assert borrow_loans.main([*MSISDNS, "--workers", workers]) == 0

    applied = [payload for path, payload in ok_bank["requests"] if path == "/apply-loan"]
    assert sorted(p["msisdn"] for p in applied) == MSISDNS
    assert {p["loanAmount"] for p in applied} == {5000}  # minAmount de l'offre
    assert len({p["transactionId"] for p in applied}) == 3
    assert sorted(loan.msisdn for loan in loans()) == MSISDNS
    assert {loan.status for loan in loans()} == {"BOOKED"}
    assert "BOOKED          3" in capsys.readouterr().out


def test_amount_and_file(client, ok_bank, tmp_path):
    msisdns = tmp_path / "msisdns.txt"
    msisdns.write_text("# test\n331000001\n\n331000002  # second\n331000001\n", encoding="utf-8")
    assert borrow_loans.main(["--file", str(msisdns), "--amount", "999999"]) == 0  # pas de contrôle de bornes

    applied = [payload for path, payload in ok_bank["requests"] if path == "/apply-loan"]
    assert sorted(p["msisdn"] for p in applied) == ["331000001", "331000001", "331000002"]
    assert {p["loanAmount"] for p in applied} == {999999}


def test_refused_eligibility_and_failed_apply(client, bank, capsys):  # noqa: F811
    bank["responses"] = {"/eligibility": (200, ELIGIBLE), "/apply-loan": httpx.ConnectError("refused")}
    assert borrow_loans.main(["331000001"]) == 0
    bank["responses"]["/eligibility"] = (200, {"responseCode": "400", "message": "Not eligible"})
    assert borrow_loans.main(["331000002"]) == 0

    out = capsys.readouterr().out
    assert "FAILED" in out and "refused" in out
    assert "ELIGIBILITY_KO" in out and "Not eligible" in out
    assert [(loan.msisdn, loan.status) for loan in loans()] == [("331000001", "FAILED")]


def test_not_configured(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "bank_apply_loan_path", "")
    assert borrow_loans.main(["331000001"]) == 1


@pytest.mark.parametrize("argv", [[], ["--amount", "0", "331000001"], ["--workers", "0", "331000001"], ["x" * 51]])
def test_invalid_arguments(client, argv):
    with pytest.raises(SystemExit):
        borrow_loans.main(argv)
