"""Merchant signup and login. The session stores a user id. The merchant comes from that row."""

import hashlib
import hmac
import secrets
from collections.abc import Mapping
from html import escape

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from voroute.store import (
    PILOT_NAME,
    DuplicateEmail,
    DuplicateToken,
    TokenReplaceRefused,
    create_account,
    find_user_by_email,
    find_user_by_id,
    replace_api_token_hash,
    token_hash,
)

MIN_PASSWORD = 10
MIN_SECRET = 32

_hasher = PasswordHasher()
_DUMMY_HASH = _hasher.hash("voroute-dummy-password-not-a-login")


class AuthError(Exception):
    def __init__(self, detail: str, status_code: int) -> None:
        self.detail = detail
        self.status_code = status_code


def require_session_secret(secret: str) -> None:
    """Refuse to build the app around a missing or short signing key."""

    if len(secret.strip()) < MIN_SECRET:
        raise RuntimeError("SESSION_SECRET is not set")


def issue_csrf(session: dict[str, object]) -> str:
    token = secrets.token_urlsafe(32)
    session["csrf_token"] = token
    return token


def establish_session(session: dict[str, object], user_id: str) -> str:
    """Replace whatever was in the cookie with this user and a fresh CSRF token."""

    token = secrets.token_urlsafe(32)
    session.clear()
    session["user_id"] = user_id
    session["csrf_token"] = token
    return token


def csrf_ok(expected: object, presented: object) -> bool:
    left = expected if isinstance(expected, str) else ""
    right = presented if isinstance(presented, str) else ""
    match = hmac.compare_digest(
        hashlib.sha256(left.encode()).digest(),
        hashlib.sha256(right.encode()).digest(),
    )
    return bool(left) and bool(right) and match


def current_merchant_id(session: Mapping[str, object]) -> str | None:
    """Merchant for this session. Request fields are not consulted."""

    user_id = session.get("user_id")
    if not isinstance(user_id, str) or not user_id:
        return None
    user = find_user_by_id(user_id)
    if user is None:
        return None
    return user.merchant_id


def signup(brand_name: str, email: str, password: str) -> tuple[str, str, str]:
    """Create the merchant and its first user. Returns user id, merchant id, and the raw token."""

    name = _brand(brand_name)
    normalized = _email(email)
    if len(password) < MIN_PASSWORD:
        raise AuthError("password must be at least 10 characters", 400)
    password_hash = _hasher.hash(password)
    for _ in range(2):
        api_token = secrets.token_urlsafe(32)
        try:
            user_id, merchant_id = create_account(
                name, normalized, password_hash, token_hash(api_token)
            )
        except DuplicateEmail as exc:
            raise AuthError("an account with that email already exists", 409) from exc
        except DuplicateToken:
            continue
        return user_id, merchant_id, api_token
    raise AuthError("could not create the account", 500)


def regenerate_api_token(merchant_id: str) -> str:
    """Mint a new intake token and overwrite this merchant's stored hash."""

    for _ in range(2):
        api_token = secrets.token_urlsafe(32)
        try:
            replace_api_token_hash(merchant_id, token_hash(api_token))
        except DuplicateToken:
            continue
        except TokenReplaceRefused as exc:
            raise AuthError("cannot replace this token", 403) from exc
        return api_token
    raise AuthError("could not replace the token", 500)


def authenticate(email: str, password: str) -> str | None:
    """Return the user id, or None for an unknown email or a wrong password."""

    normalized = email.strip().lower()
    user = find_user_by_email(normalized) if normalized else None
    if not _password_matches(None if user is None else user.password_hash, password):
        return None
    if user is None:
        return None
    return user.id


def login_form(csrf_token: str) -> str:
    return _form("/login", [("email", "text"), ("password", "password")], csrf_token)


def signup_form(csrf_token: str) -> str:
    return _form(
        "/signup",
        [("brand_name", "text"), ("email", "text"), ("password", "password")],
        csrf_token,
    )


def _password_matches(stored_hash: str | None, password: str) -> bool:
    try:
        ok = _hasher.verify(stored_hash or _DUMMY_HASH, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        ok = False
    return stored_hash is not None and ok


def _brand(name: str) -> str:
    cleaned = name.strip()
    if not cleaned or len(cleaned) > 200 or cleaned == PILOT_NAME:
        raise AuthError("brand name is invalid", 400)
    return cleaned


def _email(email: str) -> str:
    normalized = email.strip().lower()
    if " " in normalized or normalized.count("@") != 1 or len(normalized) > 255:
        raise AuthError("email is invalid", 400)
    local, domain = normalized.split("@", 1)
    if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
        raise AuthError("email is invalid", 400)
    return normalized


def _form(action: str, fields: list[tuple[str, str]], csrf_token: str) -> str:
    hidden = f'<input type="hidden" name="csrf_token" value="{escape(csrf_token)}">'
    inputs = "".join(
        f'<input name="{escape(name)}" type="{escape(kind)}">' for name, kind in fields
    )
    return f'<form method="post" action="{escape(action)}">{hidden}{inputs}</form>'
