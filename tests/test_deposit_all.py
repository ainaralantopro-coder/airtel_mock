from decimal import Decimal

import pytest
from sqlalchemy import select

import deposit_all
import generate_customers
from app.database import SessionLocal
from app.models import Customer, WalletTransaction
from tests.test_topup_notification import bank, set_defaulter  # noqa: F401  (bank est une fixture)


def test_deposits_on_every_customer(client):
    assert generate_customers.main(["3"]) == 0
    assert deposit_all.main(["1000"]) == 0
    assert deposit_all.main(["500"]) == 0  # un second passage crédite à nouveau

    with SessionLocal() as db:
        assert set(db.scalars(select(Customer.balance))) == {Decimal("1500")}
        movements = db.scalars(select(WalletTransaction)).all()
    assert len(movements) == 6
    assert {m.type for m in movements} == {WalletTransaction.TYPE_DEPOSIT}


def test_notifies_bank_only_for_defaulters(client, auth_headers, customer, bank):  # noqa: F811
    assert generate_customers.main(["2"]) == 0
    set_defaulter(client, auth_headers, customer)
    assert deposit_all.main(["1000"]) == 0

    [(path, payload)] = bank["requests"]
    assert path == "/topup-notification" and payload["msisdn"] == customer


def test_no_customer(client, capsys):
    assert deposit_all.main(["1000"]) == 0
    assert "No customer" in capsys.readouterr().out


@pytest.mark.parametrize("amount", ["0", "-5", "12.5", "abc"])
def test_invalid_amount_deposits_nothing(client, customer, amount):
    with pytest.raises(SystemExit):
        deposit_all.main(["--", amount])
    with SessionLocal() as db:
        assert db.scalar(select(Customer.balance)) == 0
