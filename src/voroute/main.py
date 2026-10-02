"""FastAPI entrypoint for the VoRoute server."""

import logging

from fastapi import FastAPI

from voroute.config import settings
from voroute.models import Order
from voroute.voflow import enqueue

logger = logging.getLogger("voroute")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    logger.addHandler(handler)

app = FastAPI(title="VoRoute", version="0.1.0")
app.state.settings = settings


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "voroute"}


@app.post("/orders")
def create_order(order: Order) -> dict[str, str]:
    logger.info(
        "order_id=%s name=%s phone=%s amount=%s cod=%s queued for confirmation call",
        order.order_id,
        order.customer_name,
        order.phone,
        order.amount,
        order.cod,
    )
    enqueue(order)
    return {"status": "queued", "order_id": order.order_id}
