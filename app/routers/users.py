from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.errors import ApiError, users_status
from app.models import Customer
from app.security import require_token

router = APIRouter(prefix="/standard/v2", tags=["users"], dependencies=[Depends(require_token)])


def format_dob(customer: Customer) -> str:
    """Format de la spec : yyyy-MM-dd HH:mm:ss.S"""
    dob = customer.dob
    return f"{dob:%Y-%m-%d %H:%M:%S}.{dob.microsecond // 100000}"


@router.get("/users/{msisdn}")
def get_user(msisdn: str, db: Session = Depends(get_db)):
    customer = db.scalar(select(Customer).where(Customer.msisdn == msisdn))
    if customer is None:
        raise ApiError(404, "User not found")

    return {
        "data": {
            "first_name": customer.first_name,
            "grade": customer.grade,
            "is_barred": customer.is_barred,
            "is_pin_set": customer.is_pin_set,
            "last_name": customer.last_name,
            "msisdn": customer.msisdn,
            "dob": format_dob(customer),
            "account_status": customer.account_status,
            "nationatility": customer.nationality,  # orthographe de la spec conservée
            "id_number": customer.id_number,
            "registration": {"status": customer.registration_status},
        },
        "status": users_status(200, "success"),
    }
