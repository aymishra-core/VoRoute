"""Signup, login, and the session cookie. These tests are the security checks for Step 2."""

import json
import logging
import os
import re
from base64 import b64decode, b64encode

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
from pydantic import ValidationError

from voroute.auth import current_merchant_id
from voroute.config import Settings, settings
from voroute.main import app, create_app
from voroute.store import (
    TokenReplaceRefused,
    account_counts,
    ensure_pilot_merchant,
    find_user_by_email,
    merchant_api_token_hash,
    order_merchant_id,
    replace_api_token_hash,
    token_hash,
)

PASSWORD = "longpassword"
EMAIL = "owner@shop.example"


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, base_url="https://testserver")


def _csrf(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


def _api_token(html: str) -> str:
    match = re.search(r'id="api-token" type="text" readonly value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


def _open(client: TestClient, path: str) -> str:
    response = client.get(path)
    assert response.status_code == 200
    return _csrf(response.text)


def _signup(
    client: TestClient,
    email: str = EMAIL,
    password: str = PASSWORD,
    brand: str = "Shop",
    **extra: str,
) -> object:
    token = _open(client, "/signup")
    return client.post(
        "/signup",
        data={
            "brand_name": brand,
            "email": email,
            "password": password,
            "csrf_token": token,
            **extra,
        },
    )


def _cookie_header(response: object) -> str:
    values = response.headers.get_list("set-cookie")  # type: ignore[attr-defined]
    return " ".join(values).lower()


def _read_session(cookie: str) -> dict[str, object]:
    signer = TimestampSigner(os.environ["SESSION_SECRET"])
    raw = signer.unsign(cookie.encode("utf-8"))
    loaded = json.loads(b64decode(raw))
    assert isinstance(loaded, dict)
    return loaded


def test_signup_stores_a_lowercase_email_and_an_argon2_hash(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("DEBUG")
    before = account_counts()
    response = _signup(client, email="Owner@Shop.Example")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    html = response.text
    assert "Account created" in html
    assert "shown only once" in html
    assert "application/json" not in response.headers["content-type"]
    api_token = _api_token(html)
    assert html.count(api_token) == 2
    assert f"Authorization: Bearer {api_token}" in html
    assert PASSWORD not in html
    assert PASSWORD not in caplog.text
    assert api_token not in caplog.text
    user = find_user_by_email("owner@shop.example")
    assert user is not None
    assert user.email == "owner@shop.example"
    assert user.password_hash.startswith("$argon2id$")
    assert user.password_hash != PASSWORD
    assert user.password_hash not in caplog.text
    stored = merchant_api_token_hash(user.merchant_id) or ""
    assert token_hash(api_token) == stored
    assert api_token not in stored
    cookie = client.cookies.get("voroute_session")
    assert cookie is not None
    assert api_token not in cookie
    assert set(_read_session(cookie)) == {"user_id", "csrf_token"}
    dashboard = client.get("/app")
    login = client.get("/login")
    assert api_token not in dashboard.text
    assert api_token not in login.text
    assert "API token was shown at signup." in dashboard.text
    merchants, users = account_counts()
    assert (merchants, users) == (before[0] + 1, before[1] + 1)


def test_duplicate_email_rolls_back_the_merchant(client: TestClient) -> None:
    assert _signup(client, email="Owner@Shop.Example").status_code == 200
    before = account_counts()
    again = _signup(client, email="OWNER@shop.example", brand="Other")
    assert again.status_code == 409
    assert again.json() == {"detail": "an account with that email already exists"}
    assert account_counts() == before


def test_short_password_writes_nothing(client: TestClient) -> None:
    before = account_counts()
    response = _signup(client, password="tiny")
    assert response.status_code == 400
    assert response.json() == {"detail": "password must be at least 10 characters"}
    assert account_counts() == before


def test_unknown_email_and_wrong_password_look_the_same(client: TestClient) -> None:
    assert _signup(client).status_code == 200
    token = _open(client, "/login")
    unknown = client.post(
        "/login",
        data={"email": "nobody@shop.example", "password": PASSWORD, "csrf_token": token},
    )
    wrong = client.post(
        "/login",
        data={"email": EMAIL, "password": "not-the-password", "csrf_token": token},
    )
    assert unknown.status_code == 401
    assert unknown.status_code == wrong.status_code
    assert unknown.content == wrong.content
    assert unknown.json() == {"detail": "email or password is wrong"}


def test_login_cookie_is_httponly_secure_and_lax(client: TestClient) -> None:
    assert _signup(client).status_code == 200
    token = _open(client, "/login")
    response = client.post(
        "/login",
        data={"email": EMAIL, "password": PASSWORD, "csrf_token": token},
    )
    assert response.status_code == 200
    header = _cookie_header(response)
    assert "voroute_session=" in header
    assert "httponly" in header
    assert "samesite=lax" in header
    assert "secure" in header


def test_session_merchant_ignores_request_input(client: TestClient) -> None:
    first = _signup(client, email="a@shop.example", brand="Alpha")
    assert first.status_code == 200
    owner = find_user_by_email("a@shop.example")
    assert owner is not None
    second = _signup(client, email="b@shop.example", brand="Beta")
    assert second.status_code == 200
    other = find_user_by_email("b@shop.example")
    assert other is not None
    token = _open(client, "/login")
    response = client.post(
        "/login",
        data={
            "email": "a@shop.example",
            "password": PASSWORD,
            "csrf_token": token,
            "merchant_id": other.merchant_id,
        },
    )
    assert response.status_code == 200
    cookie = client.cookies.get("voroute_session")
    assert cookie is not None
    session = _read_session(cookie)
    assert set(session) == {"user_id", "csrf_token"}
    assert "merchant_id" not in session
    assert current_merchant_id(session) == owner.merchant_id
    stuffed = {
        "user_id": session["user_id"],
        "csrf_token": session["csrf_token"],
        "merchant_id": other.merchant_id,
    }
    assert current_merchant_id(stuffed) == owner.merchant_id
    assert current_merchant_id(stuffed) != other.merchant_id


def test_missing_session_secret_refuses_to_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_short_session_secret_refuses_to_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "session_secret", "x" * 31)
    with pytest.raises(RuntimeError, match="SESSION_SECRET"):
        create_app()
    monkeypatch.setattr(settings, "session_secret", "   ")
    with pytest.raises(RuntimeError, match="SESSION_SECRET"):
        create_app()


def test_state_changing_posts_require_the_csrf_token(client: TestClient) -> None:
    before = account_counts()
    missing = client.post(
        "/signup",
        data={"brand_name": "Shop", "email": EMAIL, "password": PASSWORD},
    )
    assert missing.status_code == 403
    assert missing.json() == {"detail": "csrf failed"}
    assert account_counts() == before
    token = _open(client, "/login")
    wrong = client.post(
        "/login",
        data={"email": EMAIL, "password": PASSWORD, "csrf_token": "not-the-token"},
    )
    assert wrong.status_code == 403
    assert wrong.json() == {"detail": "csrf failed"}
    logged_out = client.post("/logout", data={"csrf_token": token})
    assert logged_out.status_code == 401


def test_modified_cookie_does_not_authenticate(client: TestClient) -> None:
    created = _signup(client)
    assert created.status_code == 200
    csrf = _csrf(created.text)
    original = client.cookies.get("voroute_session")
    assert original is not None
    parts = original.split(".")
    parts[0] = parts[0][:-1] + ("A" if parts[0][-1] != "A" else "B")
    client.cookies.set("voroute_session", ".".join(parts))
    tampered = client.post("/logout", data={"csrf_token": csrf})
    assert tampered.status_code == 401
    assert tampered.json() == {"detail": "unauthorized"}
    client.cookies.set("voroute_session", original)
    still = client.post("/logout", data={"csrf_token": csrf})
    assert still.status_code == 200


def test_forged_cookie_signature_does_not_authenticate(client: TestClient) -> None:
    created = _signup(client)
    assert created.status_code == 200
    csrf = _csrf(created.text)
    user = find_user_by_email(EMAIL)
    assert user is not None
    signer = TimestampSigner("not-the-real-session-secret-value!!")
    payload = b64encode(
        json.dumps(
            {"user_id": user.id, "csrf_token": csrf, "merchant_id": "forged-merchant"}
        ).encode()
    )
    client.cookies.set("voroute_session", signer.sign(payload).decode())
    forged = client.post("/logout", data={"csrf_token": csrf})
    assert forged.status_code == 401
    assert find_user_by_email(EMAIL) is not None


def test_logout_clears_the_session(client: TestClient) -> None:
    created = _signup(client)
    assert created.status_code == 200
    csrf = _csrf(created.text)
    bad = client.post("/logout", data={"csrf_token": "nope"})
    assert bad.status_code == 403
    first = client.post("/logout", data={"csrf_token": csrf})
    assert first.status_code == 200
    header = _cookie_header(first)
    assert "expires=thu, 01 jan 1970" in header
    second = client.post("/logout", data={"csrf_token": csrf})
    assert second.status_code == 401


_ORDER = {
    "customer_name": "Asha",
    "phone": "+919876543210",
    "amount": 499.0,
    "cod": True,
}


def test_regenerate_without_a_session_redirects_to_login(client: TestClient) -> None:
    response = client.post(
        "/app/regenerate-token",
        data={"csrf_token": "missing-session"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["location"].endswith("/login")


def test_regenerate_rejects_a_bad_csrf_token(client: TestClient) -> None:
    created = _signup(client)
    user = find_user_by_email(EMAIL)
    assert user is not None
    before = merchant_api_token_hash(user.merchant_id)
    missing = client.post("/app/regenerate-token", data={})
    wrong = client.post(
        "/app/regenerate-token", data={"csrf_token": "not-the-token"}
    )
    assert missing.status_code == 403
    assert wrong.status_code == 403
    assert missing.json() == {"detail": "csrf failed"}
    assert wrong.json() == missing.json()
    assert merchant_api_token_hash(user.merchant_id) == before
    assert _api_token(created.text) not in missing.text


def test_regenerate_replaces_the_token_and_invalidates_the_old_one(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    created = _signup(client)
    old = _api_token(created.text)
    user = find_user_by_email(EMAIL)
    assert user is not None
    board = client.get("/app")
    assert "Regenerate API token" in board.text
    assert "This invalidates your current token" in board.text
    assert old not in board.text
    caplog.set_level(logging.DEBUG)
    response = client.post(
        "/app/regenerate-token", data={"csrf_token": _csrf(board.text)}
    )
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "New API token" in response.text
    assert "Account created" not in response.text
    new = _api_token(response.text)
    assert new != old
    assert response.text.count(new) == 2
    assert old not in response.text
    stored = merchant_api_token_hash(user.merchant_id) or ""
    assert token_hash(new) == stored
    assert new not in stored
    assert new not in caplog.text
    assert old not in caplog.text
    cookie = client.cookies.get("voroute_session")
    assert cookie is not None
    assert new not in cookie
    assert set(_read_session(cookie)) == {"user_id", "csrf_token"}
    assert new not in client.get("/app").text

    rejected = client.post(
        "/orders",
        json={**_ORDER, "order_id": "ORD-OLD"},
        headers={"Authorization": f"Bearer {old}"},
    )
    assert rejected.status_code == 401
    assert rejected.json() == {"detail": "unauthorized"}
    queued = client.post(
        "/orders",
        json={**_ORDER, "order_id": "ORD-NEW", "merchant_id": "someone-else"},
        headers={"Authorization": f"Bearer {new}"},
    )
    assert queued.status_code == 200
    assert order_merchant_id("ORD-NEW") == user.merchant_id
    assert order_merchant_id("ORD-NEW") != "someone-else"


def test_regenerate_cannot_change_another_merchants_token() -> None:
    alpha = TestClient(app, base_url="https://testserver")
    beta = TestClient(app, base_url="https://testserver")
    old_a = _api_token(_signup(alpha, email="a@shop.example", brand="Alpha").text)
    old_b = _api_token(_signup(beta, email="b@shop.example", brand="Beta").text)
    user_a = find_user_by_email("a@shop.example")
    user_b = find_user_by_email("b@shop.example")
    assert user_a is not None and user_b is not None
    hash_b = merchant_api_token_hash(user_b.merchant_id)
    page = alpha.get("/app")
    response = alpha.post(
        "/app/regenerate-token",
        data={"csrf_token": _csrf(page.text), "merchant_id": user_b.merchant_id},
    )
    assert response.status_code == 200
    new_a = _api_token(response.text)
    assert token_hash(new_a) == merchant_api_token_hash(user_a.merchant_id)
    assert merchant_api_token_hash(user_b.merchant_id) == hash_b
    assert token_hash(old_b) == hash_b
    still_b = beta.post(
        "/orders",
        json={**_ORDER, "order_id": "ORD-BETA"},
        headers={"Authorization": f"Bearer {old_b}"},
    )
    assert still_b.status_code == 200
    assert order_merchant_id("ORD-BETA") == user_b.merchant_id
    rejected = alpha.post(
        "/orders",
        json={**_ORDER, "order_id": "ORD-ALPHA-OLD"},
        headers={"Authorization": f"Bearer {old_a}"},
    )
    assert rejected.status_code == 401
    queued = alpha.post(
        "/orders",
        json={**_ORDER, "order_id": "ORD-ALPHA-NEW"},
        headers={"Authorization": f"Bearer {new_a}"},
    )
    assert queued.status_code == 200
    assert order_merchant_id("ORD-ALPHA-NEW") == user_a.merchant_id


def test_pilot_token_hash_cannot_be_replaced() -> None:
    merchant_id = ensure_pilot_merchant()
    before = merchant_api_token_hash(merchant_id)
    with pytest.raises(TokenReplaceRefused):
        replace_api_token_hash(merchant_id, token_hash("not-the-shared-key"))
    assert merchant_api_token_hash(merchant_id) == before
