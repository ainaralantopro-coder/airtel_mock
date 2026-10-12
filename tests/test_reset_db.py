import json

import pytest
from sqlalchemy import func, select

import reset_db
from app.database import SessionLocal
from app.models import CallReceive, Customer
from bank_mock import store
from bank_mock.config import get_settings as get_bank_settings


def count(model) -> int:
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(model))


def test_reset_empties_all_tables(client, customer, token):
    assert count(Customer) == 1 and count(CallReceive) == 1
    assert reset_db.main(["--yes"]) == 0
    assert count(Customer) == 0 and count(CallReceive) == 0


def test_reset_asks_for_confirmation(client, customer, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda prompt: "no")
    assert reset_db.main([]) == 1
    assert count(Customer) == 1
    assert "Cancelled" in capsys.readouterr().out

    monkeypatch.setattr("builtins.input", lambda prompt: "yes")
    assert reset_db.main([]) == 0
    assert count(Customer) == 0


@pytest.fixture
def loans_file(tmp_path, monkeypatch):
    path = tmp_path / "loans.json"
    path.write_text(json.dumps({"APC1": {"msisdn": "1", "status": "BOOKED"}}), encoding="utf-8")
    monkeypatch.setattr(get_bank_settings(), "loans_file", str(path))
    yield path
    store.LOANS.clear()


def test_reset_bank_loans(loans_file, monkeypatch):
    monkeypatch.setattr(reset_db, "_bank_mock_running", lambda: False)
    assert reset_db.main(["--yes"]) == 0
    assert json.loads(loans_file.read_text(encoding="utf-8")) != {}  # sans --bank : fichier intact

    assert reset_db.main(["--yes", "--bank"]) == 0
    assert json.loads(loans_file.read_text(encoding="utf-8")) == {}


def test_reset_bank_refused_while_bank_mock_runs(client, customer, loans_file, monkeypatch):
    monkeypatch.setattr(reset_db, "_bank_mock_running", lambda: True)
    assert reset_db.main(["--yes", "--bank"]) == 1
    assert count(Customer) == 1
    assert json.loads(loans_file.read_text(encoding="utf-8")) != {}
