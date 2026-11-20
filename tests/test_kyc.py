import re

from sqlalchemy import select

from app.database import SessionLocal
from app.models import CallReceive

KYC_HEADERS = {"X-Country": "MG", "X-Currency": "MGA"}


def url(msisdn: str) -> str:
    return f"/loan-kyc/v1/user/{msisdn}"


def test_get_user(client, auth_headers, customer):
    client.post("/wallet/deposit", data={"msisdn": customer, "amount": "19519217"})
    r = client.get(url(customer), headers={**auth_headers, **KYC_HEADERS})
    assert r.status_code == 200
    body = r.json()
    assert re.fullmatch(r"PT\d{6}\.\d{4}\.\d{6}", body["data"].pop("user_id"))
    assert body == {
        "data": {
            "fic": "0.00",
            "id_number": "125123455522",
            "is_barred": False,
            "gender": "",
            "last_name": "Test1",
            "language": "fr",
            "account_status": "Y",
            "middle_name": "",
            "is_pin_set": True,
            "last_pin_reset_date": "",
            "frozen_amount": "0.00",
            "nationality": "MG",
            "balance": "19519217.00",
            "created_on": "",
            "dob": "1990-05-12 00:00:00.0",
            "reg_status": "SUBS",
            "grade": "SUBS",
            "id_type": "NATIONAL_ID",
            "msisdn": "997739692",
            "first_name": "Dealer",
        },
        "status": {"response_code": "DP03670000001", "code": "200", "success": True, "message": "success"},
    }


def test_user_id_is_stable(client, auth_headers, customer):
    first = client.get(url(customer), headers=auth_headers).json()["data"]["user_id"]
    assert client.get(url(customer), headers=auth_headers).json()["data"]["user_id"] == first


def test_headers_optional(client, auth_headers, customer):
    assert client.get(url(customer), headers=auth_headers).status_code == 200


def test_not_found(client, auth_headers):
    r = client.get(url("123456789"), headers=auth_headers)
    assert r.status_code == 404
    assert r.json() == {"data": None, "status": {
        "response_code": "DP03674040001", "code": "404", "success": False, "message": "User not found",
    }}


def test_requires_token_and_is_logged(client, auth_headers, customer):
    r = client.get(url(customer), headers=KYC_HEADERS)
    assert r.status_code == 401
    assert r.json()["status"]["response_code"] == "DP03674010001"
    client.get(url(customer), headers=auth_headers)
    with SessionLocal() as db:
        logged = db.scalars(select(CallReceive.response_status).where(CallReceive.path == url(customer))).all()
    assert logged == [401, 200]
