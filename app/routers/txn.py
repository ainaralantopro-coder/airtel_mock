"""Transaction Enquiry : la banque demande où en est une transaction de prêt côté Airtel."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.errors import ApiError, notify_status
from app.models import Loan
from app.schemas import TxnEnquiryRequest
from app.security import require_token

router = APIRouter(prefix="/term-loans/v1", tags=["transactions"], dependencies=[Depends(require_token)])

TXN_PROCESSED = "PROCESSED"  # prêt versé au client (Disburse OK)
TXN_FAILED = "FAILED"  # versement échoué / prêt annulé (Disburse KO), ou demande de prêt échouée
TXN_PENDING = "PENDING"  # prêt accordé, pas encore versé


@router.post("/txn/enquiry")
def txn_enquiry(body: TxnEnquiryRequest, db: Session = Depends(get_db)):
    loan = db.scalar(select(Loan).where(Loan.transaction_id == body.txn_id))
    if loan is None or loan.loan_id != body.loan_id:
        raise ApiError(404, "Transaction not found")

    return {
        "data": {"txn_id": loan.transaction_id, "txn_status": txn_status(loan), "loan_id": loan.loan_id},
        "status": notify_status(200, "Success"),
    }


def txn_status(loan: Loan) -> str:
    if loan.is_disbursed:
        return TXN_PROCESSED
    if loan.status in (Loan.STATUS_CANCELLED, Loan.STATUS_FAILED):
        return TXN_FAILED
    return TXN_PENDING
