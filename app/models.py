from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# JSONB sur PostgreSQL, JSON générique ailleurs (tests SQLite)
JsonType = JSON().with_variant(JSONB(), "postgresql")


class Customer(Base):
    """Client Mobile Money. Les champs correspondent à la réponse GET /standard/v2/users/{msisdn}."""

    __tablename__ = "customer"

    id: Mapped[int] = mapped_column(primary_key=True)
    msisdn: Mapped[str] = mapped_column(String(9), unique=True, index=True)
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    grade: Mapped[str] = mapped_column(String(20), default="SUBS")
    is_barred: Mapped[bool] = mapped_column(Boolean, default=False)
    is_pin_set: Mapped[bool] = mapped_column(Boolean, default=True)
    dob: Mapped[datetime] = mapped_column(DateTime)
    account_status: Mapped[str] = mapped_column(String(5), default="Y")
    nationality: Mapped[str] = mapped_column(String(5), default="MG")
    id_number: Mapped[str] = mapped_column(String(50))
    registration_status: Mapped[str] = mapped_column(String(20), default="SUBS")

    opted_in: Mapped[bool] = mapped_column(Boolean, default=False)
    opted_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MessageSent(Base):
    """Notification reçue de la banque (POST /term-loans/v1/notify), à destination du client."""

    __tablename__ = "message_sent"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id"), index=True)
    msisdn: Mapped[str] = mapped_column(String(9), index=True)
    message_en: Mapped[str | None] = mapped_column(Text)
    message_fr: Mapped[str | None] = mapped_column(Text)
    message_es: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Loan(Base):
    """Demande de prêt envoyée à la banque (Apply Loan), réussie ou non."""

    __tablename__ = "loan"

    STATUS_BOOKED = "BOOKED"
    STATUS_FAILED = "FAILED"
    STATUS_CANCELLED = "CANCELLED"  # versement échoué, prêt annulé à la banque (Cancel Loan)

    id: Mapped[int] = mapped_column(primary_key=True)
    msisdn: Mapped[str] = mapped_column(String(9), index=True)
    transaction_id: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    requested_amount: Mapped[int] = mapped_column(Integer)
    fees_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))  # feesAmount de Check Eligibility

    status: Mapped[str] = mapped_column(String(20), index=True)
    response_code: Mapped[str | None] = mapped_column(String(20))
    response_message: Mapped[str | None] = mapped_column(Text)

    # Champs "data" de la réponse Apply Loan
    loan_id: Mapped[str | None] = mapped_column(String(50), index=True)
    loan_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    loan_fees: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    outstanding_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    due_date: Mapped[datetime | None] = mapped_column(DateTime)
    tenure_id: Mapped[str | None] = mapped_column(String(20))
    tenure_name: Mapped[str | None] = mapped_column(String(100))
    interest_rate: Mapped[str | None] = mapped_column(String(20))

    # Décaissement confirmé à la banque (Confirm Loan)
    is_disbursed: Mapped[bool] = mapped_column(Boolean, default=False)
    external_transaction_id: Mapped[str | None] = mapped_column(String(50))
    disbursed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AccessToken(Base):
    """Tokens délivrés par /auth/oauth2/token."""

    __tablename__ = "access_token"

    id: Mapped[int] = mapped_column(primary_key=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    client_id: Mapped[str] = mapped_column(String(100))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CallSent(Base):
    """Journal des appels HTTP sortants (mock -> banque)."""

    __tablename__ = "call_sent"

    id: Mapped[int] = mapped_column(primary_key=True)
    flow: Mapped[str] = mapped_column(String(50), index=True)
    msisdn: Mapped[str | None] = mapped_column(String(9), index=True)
    method: Mapped[str] = mapped_column(String(10))
    url: Mapped[str] = mapped_column(Text)
    request_headers: Mapped[dict | None] = mapped_column(JsonType)
    request_body: Mapped[str | None] = mapped_column(Text)
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_headers: Mapped[dict | None] = mapped_column(JsonType)
    response_body: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CallReceive(Base):
    """Journal des appels HTTP entrants (banque -> mock)."""

    __tablename__ = "call_receive"

    id: Mapped[int] = mapped_column(primary_key=True)
    method: Mapped[str] = mapped_column(String(10))
    path: Mapped[str] = mapped_column(Text, index=True)
    query_string: Mapped[str | None] = mapped_column(Text)
    client_ip: Mapped[str | None] = mapped_column(String(64))
    request_headers: Mapped[dict | None] = mapped_column(JsonType)
    request_body: Mapped[str | None] = mapped_column(Text)
    response_status: Mapped[int] = mapped_column(Integer)
    response_body: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
