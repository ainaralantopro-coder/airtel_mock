from datetime import date

from pydantic import BaseModel, Field, field_validator, model_validator

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
