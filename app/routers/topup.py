"""API top-up exposées à la banque : statut defaulter des clients (Set defaulter status).

Le statut est enregistré dans loan_defaulter. Il n'a pas encore d'effet sur le mock : il servira aux futurs
flux de recouvrement sur les recharges (top-up notification, autostrike).
"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.errors import ApiError, topup_status
from app.models import Customer, Loan, LoanDefaulter
from app.schemas import DefaulterRequest
from app.security import require_token

router = APIRouter(prefix="/topup-notification/v1", tags=["topup"], dependencies=[Depends(require_token)])


@router.post("/update/loans-defaulter")
def set_defaulter_status(body: DefaulterRequest, db: Session = Depends(get_db)):
    msisdn = body.beneficiary.msisdn
    loan_ref = body.loan_info.id
    loan_type = body.loan_info.type

    customer = db.scalar(select(Customer).where(Customer.msisdn == msisdn))
    if customer is None:
        raise ApiError(404, "Customer not found")

    ref_filter = LoanDefaulter.loan_ref.is_(None) if loan_ref is None else LoanDefaulter.loan_ref == loan_ref
    defaulter = db.scalar(
        select(LoanDefaulter).where(
            LoanDefaulter.msisdn == msisdn, LoanDefaulter.loan_type == loan_type, ref_filter
        )
    )
    if body.defaulter_info.status:
        # Un seul prêt actif à la fois : pas de second prêt en défaut pour ce client
        other_active = select(LoanDefaulter.id).where(
            LoanDefaulter.msisdn == msisdn, LoanDefaulter.is_defaulter.is_(True)
        )
        if defaulter is not None:
            other_active = other_active.where(LoanDefaulter.id != defaulter.id)
        if db.scalar(other_active.limit(1)) is not None:
            raise ApiError(409, "Customer already has an active defaulted loan")

    if defaulter is None:
        defaulter = LoanDefaulter(customer_id=customer.id, msisdn=msisdn, loan_ref=loan_ref, loan_type=loan_type)
        db.add(defaulter)

    defaulter.amount = body.defaulter_info.amount
    defaulter.is_defaulter = body.defaulter_info.status
    if loan_type == LoanDefaulter.TYPE_TERM and loan_ref is not None:
        loan = db.scalar(select(Loan).where(Loan.loan_id == loan_ref, Loan.msisdn == msisdn))
        defaulter.loan_pk = loan.id if loan else None
    db.commit()

    return {"status": topup_status(200, "Success")}
