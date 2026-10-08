"""The dashboard is view-only and scoped to the session merchant."""

import re

import pytest
from fastapi.testclient import TestClient

from voroute.main import app
from voroute.store import dashboard_for, find_user_by_email
from voroute.voflow.dispatcher import apply_listen_result, note_speech

PASSWORD = "longpassword"
PHONE_A = "+919111110001"
PHONE_B = "+919222220002"
PHONE_B_DECLINED = "+919222220003"


def _client() -> TestClient:
    return TestClient(app, base_url="https://testserver")


def _csrf(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


def _signup(client: TestClient, email: str, brand: str) -> str:
    token = _csrf(client.get("/signup").text)
    created = client.post(
        "/signup",
        data={
            "brand_name": brand,
            "email": email,
            "password": PASSWORD,
            "csrf_token": token,
        },
    )
    assert created.status_code == 200
    match = re.search(r'id="api-token" type="text" readonly value="([^"]+)"', created.text)
    assert match is not None
    return match.group(1)


def _queue(
    client: TestClient,
    api_token: str,
    order_id: str,
    phone: str,
    name: str,
) -> None:
    response = client.post(
        "/orders",
        json={
            "order_id": order_id,
            "customer_name": name,
            "phone": phone,
            "amount": 499.0,
            "cod": True,
        },
        headers={"Authorization": f"Bearer {api_token}"},
    )
    assert response.status_code == 200


def _count(html: str, label: str) -> int:
    match = re.search(rf'data-count="{label}">(\d+)', html)
    assert match is not None
    return int(match.group(1))


def _ids(email: str) -> tuple[str, str]:
    user = find_user_by_email(email)
    assert user is not None
    board = dashboard_for(user.merchant_id)
    assert board.orders
    item = board.orders[0]
    assert item.job_id is not None
    return item.order_id, item.job_id


def test_app_hides_the_other_merchants_phone_and_counts() -> None:
    alpha = _client()
    beta = _client()
    token_a = _signup(alpha, "a@shop.example", "Alpha")
    token_b = _signup(beta, "b@shop.example", "Beta")
    _queue(alpha, token_a, "ORD-A", PHONE_A, "Asha")
    _queue(beta, token_b, "ORD-B", PHONE_B, "Bina")
    _queue(beta, token_b, "ORD-B2", PHONE_B_DECLINED, "Bina Two")
    _order_b, job_b = _ids("b@shop.example")
    confirmed = dashboard_for(find_user_by_email("b@shop.example").merchant_id)  # type: ignore[union-attr]
    confirmed_job = next(row.job_id for row in confirmed.orders if row.phone == PHONE_B)
    declined_job = next(
        row.job_id for row in confirmed.orders if row.phone == PHONE_B_DECLINED
    )
    assert confirmed_job is not None and declined_job is not None
    note_speech(confirmed_job, "CONFIRMED", "haan")
    apply_listen_result(confirmed_job)
    note_speech(declined_job, "DECLINED", "nahi")
    apply_listen_result(declined_job)

    page = alpha.get("/app")
    assert page.status_code == 200
    assert PHONE_A in page.text
    assert PHONE_B not in page.text
    assert PHONE_B_DECLINED not in page.text
    assert _count(page.text, "confirmed") == 0
    assert _count(page.text, "declined") == 0
    assert "RTO" not in page.text
    assert "Decline share" not in page.text

    user_b = find_user_by_email("b@shop.example")
    assert user_b is not None
    overridden = alpha.get(f"/app?merchant_id={user_b.merchant_id}")
    assert overridden.status_code == 200
    assert PHONE_A in overridden.text
    assert PHONE_B not in overridden.text
    assert PHONE_B_DECLINED not in overridden.text


def test_other_merchants_order_and_job_are_not_found() -> None:
    alpha = _client()
    beta = _client()
    token_a = _signup(alpha, "a2@shop.example", "Alpha Two")
    token_b = _signup(beta, "b2@shop.example", "Beta Two")
    _queue(alpha, token_a, "ORD-A2", PHONE_A, "A & B")
    _queue(beta, token_b, "ORD-B3", PHONE_B, "Bina")
    order_b, job_b = _ids("b2@shop.example")
    order_a, job_a = _ids("a2@shop.example")

    hidden_order = alpha.get(f"/app/orders/{order_b}")
    assert hidden_order.status_code == 404
    assert PHONE_B not in hidden_order.text
    assert "forbidden" not in hidden_order.text.lower()
    assert "ORD-B3" not in hidden_order.text

    hidden_job = alpha.get(f"/app/jobs/{job_b}")
    assert hidden_job.status_code == 404
    assert PHONE_B not in hidden_job.text
    assert "forbidden" not in hidden_job.text.lower()

    own_order = alpha.get(f"/app/orders/{order_a}")
    assert own_order.status_code == 200
    assert PHONE_A in own_order.text
    assert "A &amp; B" in own_order.text

    own_job = alpha.get(f"/app/jobs/{job_a}")
    assert own_job.status_code == 200
    assert PHONE_A in own_job.text

    missing = alpha.get("/app/orders/00000000-0000-0000-0000-000000000099")
    assert missing.status_code == 404
    assert missing.text == hidden_order.text
    assert PHONE_A not in missing.text
    assert PHONE_B not in missing.text


def test_dashboard_routes_redirect_without_a_session() -> None:
    client = _client()
    for path in (
        "/app",
        "/app/orders/00000000-0000-0000-0000-000000000099",
        "/app/jobs/00000000-0000-0000-0000-000000000099",
    ):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"].endswith("/login")


def test_empty_dashboard_has_no_specimen_row() -> None:
    client = _client()
    _signup(client, "empty@shop.example", "Empty")
    page = client.get("/app")
    assert page.status_code == 200
    assert "No orders yet." in page.text
    assert "SPECIMEN" not in page.text
    assert "Decline share" not in page.text
    assert _count(page.text, "confirmed") == 0


def test_decline_share_appears_only_when_a_call_was_decisive() -> None:
    client = _client()
    token = _signup(client, "share@shop.example", "Share")
    _queue(client, token, "ORD-YES", "+919333330001", "Yes")
    _queue(client, token, "ORD-NO", "+919333330002", "No")
    board = dashboard_for(find_user_by_email("share@shop.example").merchant_id)  # type: ignore[union-attr]
    yes = next(row.job_id for row in board.orders if row.external_order_id == "ORD-YES")
    no = next(row.job_id for row in board.orders if row.external_order_id == "ORD-NO")
    assert yes is not None and no is not None
    note_speech(yes, "CONFIRMED", "haan")
    apply_listen_result(yes)
    note_speech(no, "DECLINED", "nahi")
    apply_listen_result(no)
    page = client.get("/app")
    assert page.status_code == 200
    assert "Decline share 50.0%" in page.text
    assert "RTO" not in page.text
    assert "place" not in page.text.lower()
    assert "retry" not in page.text.lower()


def test_cockpit_stylesheet_uses_the_landing_palette() -> None:
    response = _client().get("/static/cockpit.css")
    assert response.status_code == 200
    assert "#0c0d0c" in response.text
    assert "#c6f54e" in response.text


def test_store_has_no_unscoped_order_list() -> None:
    import voroute.store as store

    assert not hasattr(store, "list_orders")
