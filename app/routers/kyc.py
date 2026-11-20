"""Loan KYC : fiche du client avec le solde de son wallet (GET /loan-kyc/v1/user/{msisdn}, voir balance-enquiry.yml).

Les champs sans équivalent dans le mock ont une valeur fixe : gender, middle_name, last_pin_reset_date et
created_on vides, language "fr", id_type "NATIONAL_ID", fic et frozen_amount à "0.00". Les headers X-Country
et X-Currency sont acceptés sans contrôle.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.errors import ApiError, kyc_status
from app.models import Customer
from app.routers.users import format_dob
from app.security import require_token

router = APIRouter(prefix="/loan-kyc/v1", tags=["users"], dependencies=[Depends(require_token)])


@router.get("/user/{msisdn}")
def get_user(msisdn: str, db: Session = Depends(get_db)):
    customer = db.scalar(select(Customer).where(Customer.msisdn == msisdn))
    if customer is None:
        raise ApiError(404, "User not found")

    return {
        "data": {
            "fic": "0.00",
            "id_number": customer.id_number,
            "is_barred": customer.is_barred,
            "gender": "",
            "last_name": customer.last_name,
            "language": "fr",
            "account_status": customer.account_status,
            "middle_name": "",
            "is_pin_set": customer.is_pin_set,
            "last_pin_reset_date": "",
            "frozen_amount": "0.00",
            "nationality": customer.nationality,
            "balance": f"{customer.balance:.2f}",
            "user_id": _user_id(customer),
            "created_on": "",
            "dob": format_dob(customer),
            "reg_status": customer.registration_status,
            "grade": customer.grade,
            "id_type": "NATIONAL_ID",
            "msisdn": customer.msisdn,
            "first_name": customer.first_name,
        },
        "status": kyc_status(200, "success"),
    }


def _user_id(customer: Customer) -> str:
    """Identifiant au format d'Airtel (PT260319.1152.121739), stable pour un client : date de création + id."""
    return f"PT{customer.created_at:%y%m%d.%H%M}.{customer.id:06d}"
