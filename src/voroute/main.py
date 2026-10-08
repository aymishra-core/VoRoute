"""FastAPI entrypoint for the VoRoute server."""

import hashlib
import hmac
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from voroute.auth import (
    AuthError,
    CsrfError,
    authenticate,
    csrf_ok,
    current_merchant_id,
    establish_session,
    issue_csrf,
    regenerate_api_token,
    require_session_secret,
    signup,
)
from voroute.config import settings
from voroute.db import prepare_database
from voroute.models import Order
from voroute.store import (
    DuplicateOrder,
    dashboard_for,
    ensure_pilot_merchant,
    job_for_merchant,
    merchant_id_for_token,
    order_for_merchant,
)
from voroute.voflow import enqueue
from voroute.voline.listen import router as listen_router
from voroute.voline.twiml import router as voice_router

_PACKAGE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=_PACKAGE / "templates")


def _inr(amount: object) -> str:
    number = float(amount)  # type: ignore[arg-type]
    if number.is_integer():
        return f"₹{int(number)}"
    return f"₹{number:.2f}"


def _when(value: object) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    return str(value)


templates.env.filters["inr"] = _inr
templates.env.filters["when"] = _when

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
    application.mount(
        "/static",
        StaticFiles(directory=_PACKAGE / "static"),
        name="static",
    )
    application.include_router(voice_router)
    application.include_router(listen_router)
    _routes(application)

    @application.exception_handler(CsrfError)
    async def _on_csrf(request: Request, _exc: CsrfError) -> HTMLResponse:
        return _csrf_failed(request)

    return application


def _routes(application: FastAPI) -> None:
    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "voroute"}

    @application.get("/login", response_model=None)
    def show_login(request: Request) -> HTMLResponse | RedirectResponse:
        if current_merchant_id(request.session) is not None:
            return RedirectResponse("/app", status_code=303)
        return templates.TemplateResponse(
            request,
            "login.html",
            {"csrf_token": issue_csrf(request.session)},
        )

    @application.get("/signup", response_model=None)
    def show_signup(request: Request) -> HTMLResponse | RedirectResponse:
        if current_merchant_id(request.session) is not None:
            return RedirectResponse("/app", status_code=303)
        return templates.TemplateResponse(
            request,
            "signup.html",
            {"csrf_token": issue_csrf(request.session)},
        )

    @application.get("/app")
    def dashboard(request: Request) -> HTMLResponse:
        merchant_id = _merchant_or_login(request)
        if not isinstance(merchant_id, str):
            return merchant_id
        return templates.TemplateResponse(
            request,
            "app.html",
            {
                "dashboard": dashboard_for(merchant_id),
                "csrf_token": request.session.get("csrf_token", ""),
            },
        )

    @application.get("/app/orders/{order_id}")
    def order_page(request: Request, order_id: str) -> HTMLResponse:
        merchant_id = _merchant_or_login(request)
        if not isinstance(merchant_id, str):
            return merchant_id
        order = order_for_merchant(merchant_id, order_id)
        if order is None:
            return _not_found(request)
        return templates.TemplateResponse(
            request,
            "order.html",
            {"order": order, "csrf_token": request.session.get("csrf_token", "")},
        )

    @application.get("/app/jobs/{job_id}")
    def job_page(request: Request, job_id: str) -> HTMLResponse:
        merchant_id = _merchant_or_login(request)
        if not isinstance(merchant_id, str):
            return merchant_id
        job = job_for_merchant(merchant_id, job_id)
        if job is None:
            return _not_found(request)
        return templates.TemplateResponse(
            request,
            "job.html",
            {"job": job, "csrf_token": request.session.get("csrf_token", "")},
        )

    @application.post("/signup", response_class=HTMLResponse)
    def create_account(
        request: Request,
        brand_name: str = Form(""),
        email: str = Form(""),
        password: str = Form(""),
        csrf_token: str = Form(""),
    ) -> HTMLResponse:
        if not csrf_ok(request.session.get("csrf_token"), csrf_token):
            raise CsrfError()
        try:
            user_id, _merchant_id, api_token = signup(brand_name, email, password)
        except AuthError as exc:
            if exc.status_code == 409:
                return templates.TemplateResponse(
                    request,
                    "signup_exists.html",
                    {"email": email},
                    status_code=409,
                )
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
        rotated = establish_session(request.session, user_id)
        return templates.TemplateResponse(
            request,
            "created.html",
            {
                "api_token": api_token,
                "csrf_token": rotated,
                "heading": "Account created",
            },
        )

    @application.post("/app/regenerate-token", response_class=HTMLResponse)
    def regenerate_token(
        request: Request, csrf_token: str = Form("")
    ) -> HTMLResponse:
        merchant_id = _merchant_or_login(request)
        if not isinstance(merchant_id, str):
            return merchant_id
        if not csrf_ok(request.session.get("csrf_token"), csrf_token):
            raise CsrfError()
        try:
            api_token = regenerate_api_token(merchant_id)
        except AuthError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
        rotated = issue_csrf(request.session)
        return templates.TemplateResponse(
            request,
            "created.html",
            {
                "api_token": api_token,
                "csrf_token": rotated,
                "heading": "New API token",
            },
        )

    @application.post("/login", response_model=None)
    def log_in(
        request: Request,
        email: str = Form(""),
        password: str = Form(""),
        csrf_token: str = Form(""),
    ) -> HTMLResponse | dict[str, str]:
        if not csrf_ok(request.session.get("csrf_token"), csrf_token):
            raise CsrfError()
        user_id = authenticate(email, password)
        if user_id is None:
            raise HTTPException(status_code=401, detail="email or password is wrong")
        rotated = establish_session(request.session, user_id)
        return {"status": "ok", "csrf_token": rotated}

    @application.post("/logout", response_model=None)
    def log_out(request: Request, csrf_token: str = Form("")) -> HTMLResponse | dict[str, str]:
        if current_merchant_id(request.session) is None:
            raise HTTPException(status_code=401, detail="unauthorized")
        if not csrf_ok(request.session.get("csrf_token"), csrf_token):
            raise CsrfError()
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


def _merchant_or_login(request: Request) -> str | HTMLResponse:
    merchant_id = current_merchant_id(request.session)
    if merchant_id is None:
        return RedirectResponse("/login", status_code=302)
    return merchant_id


def _not_found(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "not_found.html", status_code=404
    )


def _csrf_failed(request: Request) -> HTMLResponse:
    signed_in = current_merchant_id(request.session) is not None
    return templates.TemplateResponse(
        request,
        "csrf.html",
        {
            "next_href": "/app" if signed_in else "/login",
            "next_label": "Go to dashboard" if signed_in else "Log in",
        },
        status_code=400,
    )


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
