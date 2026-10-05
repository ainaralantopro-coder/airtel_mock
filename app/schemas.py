import re
from datetime import date

from pydantic import BaseModel, Field, field_validator, model_validator

MSISDN_PATTERN = r"^\d{9}$"
_msisdn_re = re.compile(MSISDN_PATTERN)


def is_valid_msisdn(value: str) -> bool:
    return bool(_msisdn_re.fullmatch(value))


class CustomerCreate(BaseModel):
    msisdn: str = Field(pattern=MSISDN_PATTERN)
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
        if not is_valid_msisdn(value):
            raise ValueError("customer_msisdn must be 9 digits")
        return value

    @model_validator(mode="after")
    def check_message(self) -> "NotifyRequest":
        if not any([self.message.en, self.message.fr, self.message.es]):
            raise ValueError("message must contain at least one of en, fr, es")
        return self
