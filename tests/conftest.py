import os
import tempfile
from pathlib import Path

import pytest

# Base SQLite jetable : doit être défini avant l'import de l'application
_db_file = Path(tempfile.mkdtemp()) / "test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file.as_posix()}"
os.environ["CLIENT_ID"] = "test-client"
os.environ["CLIENT_SECRET"] = "test-secret"
os.environ["BANK_BASE_URL"] = "http://bank.test"
os.environ["BANK_OPT_IN_PATH"] = "/opt-in"
os.environ["BANK_CHECK_ELIGIBILITY_PATH"] = "/eligibility"
os.environ["BANK_APPLY_LOAN_PATH"] = "/apply-loan"
os.environ["BANK_APPLY_LOAN_STATUS_PATH"] = "/apply-loan-status"
os.environ["BANK_CONFIRM_LOAN_PATH"] = "/confirm-loan"
os.environ["BANK_CANCEL_LOAN_PATH"] = "/cancel-loan"

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session", autouse=True)
def migrated_db():
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    yield
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_tables():
    yield
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())


@pytest.fixture
def client():
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def token(client):
    response = client.post(
        "/auth/oauth2/token",
        json={"client_id": "test-client", "client_secret": "test-secret", "grant_type": "client_credentials"},
    )
    return response.json()["access_token"]


@pytest.fixture
def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def customer(client):
    response = client.post(
        "/customers/new",
        data={
            "msisdn": "997739692",
            "first_name": "Dealer",
            "last_name": "Test1",
            "dob": "1990-05-12",
            "id_number": "125123455522",
            "nationality": "MG",
            "grade": "SUBS",
            "account_status": "Y",
            "registration_status": "SUBS",
            "is_pin_set": "on",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    return "997739692"
