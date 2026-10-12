import json

import pytest
from sqlalchemy import func, select

import reset_db
from app.database import SessionLocal
from app.models import CallReceive, CallSent, Customer
from bank_mock import store
from bank_mock.config import get_settings as get_bank_settings


def count(model) -> int:
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(model))


@pytest.fixture
def data(client, customer, token):
    """Un client, un appel reçu (le token) et un appel envoyé."""
    with SessionLocal() as db:
        db.add(CallSent(flow="opt_in", msisdn=customer, method="POST", url="http://bank.test/opt-in"))
        db.commit()
    assert (count(Customer), count(CallSent), count(CallReceive)) == (1, 1, 1)


@pytest.mark.parametrize(
    "flags, call_sent_left, call_receive_left",
    [
        ([], 1, 1),
        (["--call-sent"], 0, 1),
        (["--call-receive"], 1, 0),
        (["--call-sent", "--call-receive"], 0, 0),
        (["--calls"], 0, 0),
    ],
)
def test_reset_call_logs_on_demand(data, capsys, flags, call_sent_left, call_receive_left):
    assert reset_db.main(["--yes", *flags]) == 0
    assert (count(Customer), count(CallSent), count(CallReceive)) == (0, call_sent_left, call_receive_left)
    if call_sent_left or call_receive_left:
        assert "Kept:" in capsys.readouterr().out


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
