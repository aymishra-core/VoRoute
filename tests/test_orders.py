import pytest
from fastapi.testclient import TestClient

from voroute.main import app

client = TestClient(app)

VALID_ORDER = {
    "order_id": "ORD-1001",
    "customer_name": "Asha",
    "phone": "+919876543210",
    "amount": 499.0,
    "cod": True,
}


def test_valid_order_is_queued() -> None:
    response = client.post("/orders", json=VALID_ORDER)
    assert response.status_code == 200
    assert response.json() == {"status": "queued", "order_id": "ORD-1001"}


def test_missing_fields_return_422() -> None:
    response = client.post("/orders", json={"order_id": "ORD-1001"})
    assert response.status_code == 422


def test_invalid_amount_returns_422() -> None:
    payload = {**VALID_ORDER, "amount": "not-a-number"}
    response = client.post("/orders", json=payload)
    assert response.status_code == 422


@pytest.mark.parametrize("phone", ["919876543210", "", "  "])
def test_phone_without_plus_is_rejected(phone: str) -> None:
    payload = {**VALID_ORDER, "phone": phone}
    response = client.post("/orders", json=payload)
    assert response.status_code == 422
