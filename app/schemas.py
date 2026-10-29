from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, StrictBool, field_validator, model_validator

# Champ libre : seule la longueur est bornée, pour tenir dans les colonnes msisdn
MSISDN_MAX_LENGTH = 50


def is_valid_msisdn(value: str) -> bool:
    return 0 < len(value) <= MSISDN_MAX_LENGTH


class CustomerCreate(BaseModel):
    msisdn: str = Field(min_length=1, max_length=MSISDN_MAX_LENGTH)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    dob: date
    id_number: str = Field(min_length=1, max_length=50)
    grade: str = Field(default="SUBS", min_length=1, max_length=20)
    account_status: str = Field(default="Y", min_length=1, max_length=5)
    nationality: str = Field(default="MG", min_length=1, max_length=5)
    registration_status: str = Field(default="SUBS", min_length=1, max_length=20)
    is_barred: bool = False
    is_pin_set: bool = True

    @field_validator("*", mode="before")
    @classmethod
    def strip_strings(cls, value):
        return value.strip() if isinstance(value, str) else value


class TokenRequest(BaseModel):
    client_id: str | None = None
    client_secret: str | None = None
    grant_type: str | None = None


class NotifyMessage(BaseModel):
    en: str | None = None
    fr: str | None = None
    es: str | None = None


class NotifyRequest(BaseModel):
    customer_msisdn: str
    message: NotifyMessage

    @field_validator("customer_msisdn")
    @classmethod
    def check_msisdn(cls, value: str) -> str:
        value = value.strip()
        if not is_valid_msisdn(value):
            raise ValueError(f"customer_msisdn must be 1 to {MSISDN_MAX_LENGTH} characters")
        return value

    @model_validator(mode="after")
    def check_message(self) -> "NotifyRequest":
        if not any([self.message.en, self.message.fr, self.message.es]):
            raise ValueError("message must contain at least one of en, fr, es")
        return self


class TxnEnquiryRequest(BaseModel):
    txn_id: str  # transactionId envoyé à la banque dans Apply Loan
    loan_id: str

    @field_validator("txn_id", "loan_id")
    @classmethod
    def required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value


class DefaulterLoanInfo(BaseModel):
    id: str | None = None  # facultatif
    type: Literal["OVERDRAFT", "TERM"]

    @field_validator("id")
    @classmethod
    def empty_to_none(cls, value: str | None) -> str | None:
        value = value.strip() if value is not None else None
        return value or None


class DefaulterBeneficiary(BaseModel):
    msisdn: str

    @field_validator("msisdn")
    @classmethod
    def check_msisdn(cls, value: str) -> str:
        value = value.strip()
        if not is_valid_msisdn(value):
            raise ValueError(f"msisdn must be 1 to {MSISDN_MAX_LENGTH} characters")
        return value


class DefaulterInfo(BaseModel):
    amount: Decimal
    status: StrictBool

    @field_validator("amount", mode="before")
    @classmethod
    def parse_amount(cls, value):
        # Chaîne ("400") comme dans la spec, ou nombre ; jamais un booléen
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise ValueError("amount must be a number or a numeric string")
        return value.strip() if isinstance(value, str) else value

    @field_validator("amount")
    @classmethod
    def check_amount(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value < 0:
            raise ValueError("amount must be a number >= 0")
        return value


class DefaulterRequest(BaseModel):
    loan_info: DefaulterLoanInfo
    beneficiary: DefaulterBeneficiary
    defaulter_info: DefaulterInfo


AUTOSTRIKE_MAX_AMOUNT = Decimal(10) ** 15  # même plafond que les opérations wallet


class AutostrikeSubscriber(BaseModel):
    msisdn: str
    country: str | None = None
    currency: str | None = None

    @field_validator("msisdn")
    @classmethod
    def check_msisdn(cls, value: str) -> str:
        value = value.strip()
        if not is_valid_msisdn(value):
            raise ValueError(f"msisdn must be 1 to {MSISDN_MAX_LENGTH} characters")
        return value


class AutostrikeTransaction(BaseModel):
    id: str = Field(max_length=100)
    amount: Decimal
    country: str | None = None
    currency: str | None = None

    @field_validator("id")
    @classmethod
    def check_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value

    @field_validator("amount", mode="before")
    @classmethod
    def parse_amount(cls, value):
        # Chaîne ("1000") comme dans la spec, ou nombre ; jamais un booléen
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise ValueError("amount must be a number or a numeric string")
        return value.strip() if isinstance(value, str) else value

    @field_validator("amount")
    @classmethod
    def check_amount(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value <= 0 or value != value.to_integral_value():
            raise ValueError("amount must be a whole number greater than 0")
        if value >= AUTOSTRIKE_MAX_AMOUNT:
            raise ValueError(f"amount must be less than {AUTOSTRIKE_MAX_AMOUNT:f}")
        return value


class AutostrikeRequest(BaseModel):
    reference: str = Field(max_length=100)
    subscriber: AutostrikeSubscriber
    transaction: AutostrikeTransaction

    @field_validator("reference")
    @classmethod
    def check_reference(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value
