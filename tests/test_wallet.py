from decimal import Decimal

import pytest
from sqlalchemy import select

from app import wallet
from app.database import SessionLocal
from app.models import Customer, WalletTransaction
from app.web import format_ariary


def balance(msisdn: str) -> Decimal:
    with SessionLocal() as db:
        return db.scalar(select(Customer.balance).where(Customer.msisdn == msisdn))


def history(msisdn: str) -> list[WalletTransaction]:
    with SessionLocal() as db:
        return db.scalars(select(WalletTransaction).where(WalletTransaction.msisdn == msisdn)
                          .order_by(WalletTransaction.id)).all()


def test_new_customer_starts_at_zero(client, customer):
    assert balance(customer) == 0
    page = client.get(f"/wallet?msisdn={customer}").text
    assert format_ariary(0) in page and "No operation yet." in page


def test_deposit_then_withdraw(client, customer):
    r = client.post("/wallet/deposit", data={"msisdn": customer, "amount": "5000", "description": "Cash in"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith(f"/wallet?msisdn={customer}&done=WLT")

    page = client.get(r.headers["location"]).text
    assert "Deposit of" in page and f"New balance: {format_ariary(5000)}" in page

    r = client.post("/wallet/withdraw", data={"msisdn": customer, "amount": "1200"})
    assert r.status_code == 200 and "Withdrawal of" in r.text
    assert balance(customer) == Decimal("3800")

    deposit, withdrawal = history(customer)
    assert (deposit.type, deposit.amount, deposit.balance_after, deposit.description) == (
        "DEPOSIT", Decimal("5000"), Decimal("5000"), "Cash in"
    )
    assert (withdrawal.type, withdrawal.amount, withdrawal.balance_after, withdrawal.description) == (
        "WITHDRAWAL", Decimal("1200"), Decimal("3800"), None
    )
    assert deposit.reference != withdrawal.reference

    page = client.get(f"/wallet?msisdn={customer}").text
    assert deposit.reference in page and withdrawal.reference in page and "Cash in" in page


def test_withdraw_more_than_balance_is_refused(client, customer):
    client.post("/wallet/deposit", data={"msisdn": customer, "amount": "1000"})
    r = client.post("/wallet/withdraw", data={"msisdn": customer, "amount": "1001", "description": "x"})
    assert r.status_code == 400
    assert "Insufficient balance" in r.text and 'value="1001"' in r.text
    assert balance(customer) == Decimal("1000") and len(history(customer)) == 1

    client.post("/wallet/withdraw", data={"msisdn": customer, "amount": "1000"})
    assert balance(customer) == 0


@pytest.mark.parametrize("amount", ["0", "-5", "12.5", "abc", "", "1e30", "NaN"])
@pytest.mark.parametrize("operation", ["deposit", "withdraw"])
def test_invalid_amount(client, customer, operation, amount):
    r = client.post(f"/wallet/{operation}", data={"msisdn": customer, "amount": amount})
    assert r.status_code == 400
    assert history(customer) == [] and balance(customer) == 0


def test_description_too_long(client, customer):
    r = client.post("/wallet/deposit", data={"msisdn": customer, "amount": "10", "description": "x" * 256})
    assert r.status_code == 400 and "255 characters" in r.text


def test_unknown_customer(client):
    assert client.get("/wallet?msisdn=000").status_code == 404
    assert client.post("/wallet/deposit", data={"msisdn": "000", "amount": "10"}).status_code == 404


def test_service_rereads_locked_balance(client, customer):
    """Un client déjà chargé (solde périmé en mémoire) ne doit pas fausser le calcul."""
    with SessionLocal() as db:
        stale = db.scalar(select(Customer).where(Customer.msisdn == customer))
        with SessionLocal() as other:
            wallet.deposit(other, stale.id, Decimal(700))
        assert stale.balance == 0  # valeur en mémoire, périmée
        wallet.withdraw(db, stale.id, Decimal(500))
    assert balance(customer) == Decimal("200")


def test_customers_page_shows_balance_and_wallet_link(client, customer):
    client.post("/wallet/deposit", data={"msisdn": customer, "amount": "2500"})
    page = client.get("/").text
    assert format_ariary(2500) in page and f'href="/wallet?msisdn={customer}"' in page


def test_done_reference_of_another_customer_is_ignored(client, customer):
    client.post("/customers/new", data={"msisdn": "111", "first_name": "A", "last_name": "B",
                                        "dob": "1990-01-01", "id_number": "1"})
    r = client.post("/wallet/deposit", data={"msisdn": "111", "amount": "10"}, follow_redirects=False)
    reference = r.headers["location"].split("done=")[1]
    assert "Deposit of" not in client.get(f"/wallet?msisdn={customer}&done={reference}").text
