import logging
import re
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from voroute.config import settings
from voroute.db import normalized_database_url, session_factory
from voroute.main import app
from voroute.models import PHONE_E164_DETAIL, Order
from voroute.store import (
    DuplicateOrder,
    dashboard_for,
    ensure_pilot_merchant,
    find_user_by_email,
    order_merchant_id,
)
from voroute.tables import JobRow, OrderRow
from voroute.voflow import enqueue
from voroute.voflow.dispatcher import get_job
from voroute.voline.provider import set_provider

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


def test_json_merchant_id_does_not_choose_the_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {**VALID_ORDER, "order_id": "ORD-OWNER", "merchant_id": "someone-else"}
    response = client.post("/orders", json=payload, headers=_auth(monkeypatch))
    assert response.status_code == 200
    owner = order_merchant_id("ORD-OWNER")
    assert owner == ensure_pilot_merchant()
    assert owner != "someone-else"


def test_signup_token_queues_for_that_merchant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "voroute_api_key", "test-key")
    secure = TestClient(app, base_url="https://testserver")
    page = secure.get("/signup")
    match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert match is not None
    created = secure.post(
        "/signup",
        data={
            "brand_name": "Shop",
            "email": "shop@example.com",
            "password": "longpassword",
            "csrf_token": match.group(1),
            "merchant_id": "someone-else",
        },
    )
    assert created.status_code == 200
    assert "text/html" in created.headers["content-type"]
    token_match = re.search(
        r'id="api-token" type="text" readonly value="([^"]+)"', created.text
    )
    assert token_match is not None
    api_token = token_match.group(1)
    payload = {**VALID_ORDER, "order_id": "ORD-SHOP", "merchant_id": "someone-else"}
    response = secure.post(
        "/orders",
        json=payload,
        headers={"Authorization": f"Bearer {api_token}"},
    )
    assert response.status_code == 200
    owner = order_merchant_id("ORD-SHOP")
    user = find_user_by_email("shop@example.com")
    assert user is not None
    assert owner == user.merchant_id
    assert owner != ensure_pilot_merchant()
    assert owner != "someone-else"


def test_postgres_url_uses_the_psycopg_driver() -> None:
    assert (
        normalized_database_url("postgres://user:secret@db.internal/voroute")
        == "postgresql+psycopg://user:secret@db.internal/voroute"
    )
    assert (
        normalized_database_url("postgresql://user:secret@db.internal/voroute")
        == "postgresql+psycopg://user:secret@db.internal/voroute"
    )
    assert normalized_database_url("sqlite:///voroute.db") == "sqlite:///voroute.db"


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


@pytest.mark.parametrize(
    "phone",
    ["919876543210", "", "  ", "+91", "+91987654321a", "+91 98765 43210"],
)
def test_invalid_phone_is_rejected_and_creates_no_job(
    monkeypatch: pytest.MonkeyPatch, phone: str
) -> None:
    payload = {**VALID_ORDER, "phone": phone}
    response = client.post("/orders", json=payload, headers=_auth(monkeypatch))
    assert response.status_code == 422
    assert response.json() == {"detail": PHONE_E164_DETAIL}
    assert order_merchant_id("ORD-1001") is None


def test_duplicate_order_is_already_queued(monkeypatch: pytest.MonkeyPatch) -> None:
    headers = _auth(monkeypatch)
    first = client.post("/orders", json=VALID_ORDER, headers=headers)
    second = client.post("/orders", json=VALID_ORDER, headers=headers)
    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json() == {"detail": "order already queued"}


def test_concurrent_identical_orders_place_one_call() -> None:
    dials: list[str] = []

    class _Count:
        def place_call(self, to: str, job: object) -> str:
            dials.append(getattr(job, "job_id"))
            return "CA_BURST"

    set_provider(_Count())

    def once() -> int:
        try:
            enqueue(Order(**VALID_ORDER))
        except DuplicateOrder:
            return 409
        return 200

    with ThreadPoolExecutor(max_workers=3) as pool:
        codes = list(pool.map(lambda _n: once(), range(3)))

    assert sorted(codes) == [200, 409, 409]
    assert len(dials) == 1
    session = session_factory()()
    try:
        jobs = session.scalar(select(func.count()).select_from(JobRow))
        orders = session.scalar(
            select(func.count())
            .select_from(OrderRow)
            .where(OrderRow.external_order_id == "ORD-1001")
        )
    finally:
        session.close()
    assert jobs == 1
    assert orders == 1


def test_two_merchants_can_share_an_order_id() -> None:
    first = _signup("a@shop.example", "Alpha")
    second = _signup("b@shop.example", "Beta")
    payload = {**VALID_ORDER, "order_id": "ORD-SHARED"}
    alpha = first.post(
        "/orders",
        json=payload,
        headers={"Authorization": f"Bearer {first.token}"},
    )
    beta = second.post(
        "/orders",
        json=payload,
        headers={"Authorization": f"Bearer {second.token}"},
    )
    assert alpha.status_code == 200
    assert beta.status_code == 200
    session = session_factory()()
    try:
        rows = session.scalars(
            select(OrderRow).where(OrderRow.external_order_id == "ORD-SHARED")
        ).all()
    finally:
        session.close()
    assert len(rows) == 2
    assert len({row.merchant_id for row in rows}) == 2


@pytest.mark.parametrize(
    ("message", "status"),
    [
        (
            'duplicate key value violates unique constraint "uq_orders_merchant_external"',
            409,
        ),
        (
            "UNIQUE constraint failed: orders.merchant_id, orders.external_order_id",
            409,
        ),
        ("UNIQUE constraint failed: users.email", 500),
        ('duplicate key value violates unique constraint "users_email_key"', 500),
    ],
)
def test_only_the_order_constraint_maps_to_already_queued(
    monkeypatch: pytest.MonkeyPatch, message: str, status: int
) -> None:
    from voroute.store import Session

    original = Session.flush

    def flush(self: Session, objects: object = None) -> None:
        if any(isinstance(obj, OrderRow) for obj in self.new):
            raise IntegrityError("INSERT", {}, Exception(message))
        original(self, objects)

    monkeypatch.setattr(Session, "flush", flush)
    quiet = TestClient(app, raise_server_exceptions=False)
    response = quiet.post("/orders", json=VALID_ORDER, headers=_auth(monkeypatch))
    assert response.status_code == status
    if status == 409:
        assert response.json() == {"detail": "order already queued"}
    else:
        assert "order already queued" not in response.text
    assert order_merchant_id("ORD-1001") is None


def test_first_dial_failure_is_failed_on_the_dashboard(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _Down:
        def place_call(self, to: str, job: object) -> str:
            raise RuntimeError("twilio down")

    signed = _signup("fail@shop.example", "Fail")
    set_provider(_Down())
    caplog.set_level(logging.ERROR, logger="voroute.voflow")
    response = signed.post(
        "/orders",
        json=VALID_ORDER,
        headers={"Authorization": f"Bearer {signed.token}"},
    )
    assert response.status_code == 502
    assert response.json() == {"detail": "call could not be placed"}
    assert "voice=place-failed" in caplog.text

    user = find_user_by_email("fail@shop.example")
    assert user is not None
    board = dashboard_for(user.merchant_id)
    assert board.counts.failed == 1
    assert board.counts.unclear == 0
    assert board.counts.in_progress == 0
    assert len(board.orders) == 1
    item = board.orders[0]
    assert item.status == "FAILED"
    assert item.next_attempt_at is None
    assert item.attempt_count == 0
    assert item.job_id is not None
    stored = get_job(item.job_id)
    assert stored is not None
    assert stored.status.value == "FAILED"
    assert stored.next_attempt_at is None

    page = signed.get("/app")
    assert page.status_code == 200
    assert 'data-count="failed">1' in page.text
    assert "UNCLEAR" not in page.text
    assert "CALLING" not in page.text

    again = signed.post(
        "/orders",
        json=VALID_ORDER,
        headers={"Authorization": f"Bearer {signed.token}"},
    )
    assert again.status_code == 409
    assert dashboard_for(user.merchant_id).counts.failed == 1


class _Signed:
    def __init__(self, client: TestClient, token: str) -> None:
        self._client = client
        self.token = token

    def post(self, path: str, **kwargs: object) -> object:
        return self._client.post(path, **kwargs)

    def get(self, path: str) -> object:
        return self._client.get(path)


def _signup(email: str, brand: str) -> _Signed:
    secure = TestClient(app, base_url="https://testserver")
    page = secure.get("/signup")
    match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert match is not None
    created = secure.post(
        "/signup",
        data={
            "brand_name": brand,
            "email": email,
            "password": "longpassword",
            "csrf_token": match.group(1),
        },
    )
    assert created.status_code == 200
    token_match = re.search(
        r'id="api-token" type="text" readonly value="([^"]+)"', created.text
    )
    assert token_match is not None
    return _Signed(secure, token_match.group(1))
