"""FastAPI entrypoint for the VoRoute server."""

import hashlib
import hmac
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException

from voroute.config import settings
from voroute.db import prepare_database
from voroute.models import Order
from voroute.store import DuplicateOrder
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


app = FastAPI(title="VoRoute", version="0.1.0", lifespan=_lifespan)
app.state.settings = settings
app.include_router(voice_router)
app.include_router(listen_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "voroute"}


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


def require_api_key(authorization: str | None = Header(default=None)) -> None:
    """POST /orders only. A blank server key rejects everyone."""

    if not _key_matches(settings.voroute_api_key, _presented_bearer(authorization)):
        raise HTTPException(status_code=401, detail="unauthorized")


@app.post("/orders")
def create_order(
    order: Order, _: None = Depends(require_api_key)
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
        enqueue(order)
    except DuplicateOrder:
        raise HTTPException(status_code=409, detail="order already queued") from None
    return {"status": "queued", "order_id": order.order_id}
