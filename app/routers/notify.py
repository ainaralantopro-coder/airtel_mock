from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.errors import ApiError, notify_status
from app.models import Customer, MessageSent
from app.schemas import NotifyRequest
from app.security import require_token

router = APIRouter(prefix="/term-loans/v1", tags=["notify"], dependencies=[Depends(require_token)])


@router.post("/notify")
def notify(body: NotifyRequest, db: Session = Depends(get_db)):
    customer = db.scalar(select(Customer).where(Customer.msisdn == body.customer_msisdn))
    if customer is None:
        raise ApiError(404, "Customer not found")

    db.add(
        MessageSent(
            customer_id=customer.id,
            msisdn=customer.msisdn,
            message_en=body.message.en,
            message_fr=body.message.fr,
            message_es=body.message.es,
        )
    )
    db.commit()
    return {
        "data": {"message": "Notification sent successfully."},
        "status": notify_status(200, "Success"),
    }
