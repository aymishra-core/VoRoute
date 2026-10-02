"""Request models for order intake."""

from pydantic import BaseModel, field_validator


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
        if not phone or not phone.startswith("+"):
            raise ValueError("phone must be a non-empty E.164 number starting with +")
        return phone
