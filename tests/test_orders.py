import pytest
from fastapi.testclient import TestClient

from voroute.config import settings
from voroute.main import app

client = TestClient(app)

VALID_ORDER = {
    "order_id": "ORD-1001",
    "customer_name": "Asha",
    "phone": "+919876543210",
    "amount": 499.0,
    "cod": True,
}

_UNAUTHORIZED = {"detail": "unauthorized"}


def _auth(monkeypatch: pytest.MonkeyPatch, key: str = "test-key") -> dict[str, str]:
    monkeypatch.setattr(settings, "voroute_api_key", key)
    return {"Authorization": f"Bearer {key}"}


def test_valid_order_is_queued(monkeypatch: pytest.MonkeyPatch) -> None:
    response = client.post("/orders", json=VALID_ORDER, headers=_auth(monkeypatch))
    assert response.status_code == 200
    assert response.json() == {"status": "queued", "order_id": "ORD-1001"}


def test_missing_api_key_is_unauthorized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voroute_api_key", "test-key")
    response = client.post("/orders", json=VALID_ORDER)
    assert response.status_code == 401
    assert response.json() == _UNAUTHORIZED


def test_wrong_api_key_is_unauthorized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voroute_api_key", "test-key")
    response = client.post(
        "/orders",
        json=VALID_ORDER,
        headers={"Authorization": "Bearer wrong-key"},
    )
    assert response.status_code == 401
    assert response.json() == _UNAUTHORIZED


def test_blank_api_key_rejects_orders(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voroute_api_key", "")
    response = client.post(
        "/orders",
        json=VALID_ORDER,
        headers={"Authorization": "Bearer test-key"},
    )
    assert response.status_code == 401
    assert response.json() == _UNAUTHORIZED


def test_voice_route_does_not_require_the_orders_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "voroute_api_key", "test-key")
    response = client.post(
        "/voice/twiml/00000000-0000-0000-0000-000000000000"
    )
    assert response.status_code != 401


def test_missing_fields_return_422(monkeypatch: pytest.MonkeyPatch) -> None:
    response = client.post(
        "/orders",
        json={"order_id": "ORD-1001"},
        headers=_auth(monkeypatch),
    )
    assert response.status_code == 422


def test_invalid_amount_returns_422(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {**VALID_ORDER, "amount": "not-a-number"}
    response = client.post("/orders", json=payload, headers=_auth(monkeypatch))
    assert response.status_code == 422


@pytest.mark.parametrize("phone", ["919876543210", "", "  "])
def test_phone_without_plus_is_rejected(
    monkeypatch: pytest.MonkeyPatch, phone: str
) -> None:
    payload = {**VALID_ORDER, "phone": phone}
    response = client.post("/orders", json=payload, headers=_auth(monkeypatch))
    assert response.status_code == 422
