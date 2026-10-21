"""Request models for order intake."""

import phonenumbers
from phonenumbers import NumberParseException
from pydantic import BaseModel, field_validator

PHONE_E164_DETAIL = "phone must be E.164, e.g. +919876543210"


class Order(BaseModel):
    order_id: str
    customer_name: str
    phone: str
    amount: float
    cod: bool = True

    @field_validator("phone")
    @classmethod
    def phone_is_e164(cls, value: str) -> str:
        phone = value.strip()
        try:
            parsed = phonenumbers.parse(phone, None)
        except NumberParseException as exc:
            raise ValueError(PHONE_E164_DETAIL) from exc
        formatted = phonenumbers.format_number(
            parsed, phonenumbers.PhoneNumberFormat.E164
        )
        if formatted != phone or not phonenumbers.is_valid_number(parsed):
            raise ValueError(PHONE_E164_DETAIL)
        return phone
