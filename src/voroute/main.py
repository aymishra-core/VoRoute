"""FastAPI entrypoint for the VoRoute server."""

import hashlib
import hmac
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from starlette.middleware.sessions import SessionMiddleware

from voroute.auth import (
    AuthError,
    authenticate,
    csrf_ok,
    current_merchant_id,
    establish_session,
    issue_csrf,
    login_form,
    require_session_secret,
    signup,
    signup_form,
)
from voroute.config import settings
from voroute.db import prepare_database
from voroute.models import Order
from voroute.store import DuplicateOrder, ensure_pilot_merchant, merchant_id_for_token
from voroute.voflow import enqueue
from voroute.voline.listen import router as listen_router
from voroute.voline.twiml import router as voice_router

logger = logging.getLogger("voroute")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    logger.addHandler(handler)


@asynccontextmanager
async def _lifespan(_app: object):
    prepare_database()
    yield


def create_app() -> FastAPI:
    require_session_secret(settings.session_secret)
    application = FastAPI(title="VoRoute", version="0.1.0", lifespan=_lifespan)
    application.state.settings = settings
    application.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie="voroute_session",
        same_site="lax",
        https_only=True,
    )
    application.include_router(voice_router)
    application.include_router(listen_router)
    _routes(application)
    return application


def _routes(application: FastAPI) -> None:
    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "voroute"}

    @application.get("/login", response_class=HTMLResponse)
    def show_login(request: Request) -> str:
        return login_form(issue_csrf(request.session))

    @application.get("/signup", response_class=HTMLResponse)
    def show_signup(request: Request) -> str:
        return signup_form(issue_csrf(request.session))

    @application.post("/signup")
    def create_account(
        request: Request,
        brand_name: str = Form(""),
        email: str = Form(""),
        password: str = Form(""),
        csrf_token: str = Form(""),
    ) -> dict[str, str]:
        if not csrf_ok(request.session.get("csrf_token"), csrf_token):
            raise HTTPException(status_code=403, detail="csrf failed")
        try:
            user_id, _merchant_id, api_token = signup(brand_name, email, password)
        except AuthError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
        rotated = establish_session(request.session, user_id)
        return {"status": "ok", "api_token": api_token, "csrf_token": rotated}

    @application.post("/login")
    def log_in(
        request: Request,
        email: str = Form(""),
        password: str = Form(""),
        csrf_token: str = Form(""),
    ) -> dict[str, str]:
        if not csrf_ok(request.session.get("csrf_token"), csrf_token):
            raise HTTPException(status_code=403, detail="csrf failed")
        user_id = authenticate(email, password)
        if user_id is None:
            raise HTTPException(status_code=401, detail="email or password is wrong")
        rotated = establish_session(request.session, user_id)
        return {"status": "ok", "csrf_token": rotated}

    @application.post("/logout")
    def log_out(request: Request, csrf_token: str = Form("")) -> dict[str, str]:
        if current_merchant_id(request.session) is None:
            raise HTTPException(status_code=401, detail="unauthorized")
        if not csrf_ok(request.session.get("csrf_token"), csrf_token):
            raise HTTPException(status_code=403, detail="csrf failed")
        request.session.clear()
        return {"status": "ok"}

    @application.post("/orders")
    def create_order(
        order: Order, merchant_id: str = Depends(require_api_key)
    ) -> dict[str, str]:
        logger.info(
            "order_id=%s name=%s phone=%s amount=%s cod=%s queued for confirmation call",
            order.order_id,
            order.customer_name,
            order.phone,
            order.amount,
            order.cod,
        )
        try:
            enqueue(order, merchant_id=merchant_id)
        except DuplicateOrder:
            raise HTTPException(status_code=409, detail="order already queued") from None
        return {"status": "queued", "order_id": order.order_id}


def _presented_bearer(authorization: str | None) -> str:
    if not authorization:
        return ""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return token.strip()


def _key_matches(expected: str, presented: str) -> bool:
    if not expected or not presented:
        return False
    return hmac.compare_digest(
        hashlib.sha256(expected.encode()).digest(),
        hashlib.sha256(presented.encode()).digest(),
    )


def require_api_key(authorization: str | None = Header(default=None)) -> str:
    """POST /orders. The shared key maps to the pilot. A signup token maps to that merchant."""

    presented = _presented_bearer(authorization)
    if _key_matches(settings.voroute_api_key, presented):
        return ensure_pilot_merchant()
    merchant_id = merchant_id_for_token(presented)
    if merchant_id is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    return merchant_id


app = create_app()
