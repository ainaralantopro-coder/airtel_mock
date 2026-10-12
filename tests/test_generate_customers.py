import re
from datetime import date

from sqlalchemy import select

import generate_customers
from app.database import SessionLocal
from app.models import Customer


def all_customers() -> list[Customer]:
    with SessionLocal() as db:
        return db.scalars(select(Customer)).all()


def test_generates_customers_with_fixed_fields(client):
    assert generate_customers.main(["20"]) == 0
    customers = all_customers()
    assert len(customers) == 20
    assert len({c.msisdn for c in customers}) == 20 and len({c.id_number for c in customers}) == 20

    today = date.today()
    for c in customers:
        assert re.fullmatch(r"33\d{7}", c.msisdn)
        assert re.fullmatch(r"[1-9]\d{10}", c.id_number)
        assert c.first_name and c.last_name
        assert 18 <= (today - c.dob.date()).days // 365.25 <= 70
        assert (c.nationality, c.grade, c.account_status, c.registration_status) == ("MG", "SUBS", "N", "SUBS")
        assert c.is_pin_set is True and c.is_barred is False and c.opted_in is False


def test_avoids_existing_msisdns(client, customer):
    assert generate_customers.main(["5", "--prefix", "99773969"]) == 0  # 10 MSISDN possibles, dont 1 pris
    assert len(all_customers()) == 6

    assert generate_customers.main(["5", "--prefix", "99773969"]) == 1  # plus que 4 de libres
    assert generate_customers.main(["4", "--prefix", "99773969"]) == 0
    assert {c.msisdn for c in all_customers()} == {f"99773969{d}" for d in range(10)}


def test_seed_is_reproducible(client):
    generate_customers.main(["3", "--seed", "7"])
    first = sorted((c.msisdn, c.first_name, c.id_number) for c in all_customers())
    with SessionLocal() as db:
        for c in db.scalars(select(Customer)):
            db.delete(c)
        db.commit()
    generate_customers.main(["3", "--seed", "7"])
    assert sorted((c.msisdn, c.first_name, c.id_number) for c in all_customers()) == first


def test_dates_of_birth_are_unique(client, customer):
    assert generate_customers.main(["300"]) == 0
    dobs = [c.dob for c in all_customers()]
    assert len(dobs) == 301 and len(set(dobs)) == 301


def test_stops_when_no_date_of_birth_left(client, monkeypatch):
    monkeypatch.setattr(generate_customers, "MAX_AGE", generate_customers.MIN_AGE)  # une seule date possible
    assert generate_customers.main(["2"]) == 1
    assert all_customers() == []
    assert generate_customers.main(["1"]) == 0
    assert generate_customers.main(["1"]) == 1
    assert len(all_customers()) == 1
