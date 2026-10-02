"""Queue confirmation jobs. Decides that a call should happen, never how it is placed."""

import logging

from voroute.models import Order
from voroute.voflow.job import ConfirmationJob, JobStatus
from voroute.voflow.script import build_script

logger = logging.getLogger("voroute.voflow")


def enqueue(order: Order) -> ConfirmationJob:
    job = ConfirmationJob(
        order_id=order.order_id,
        customer_name=order.customer_name,
        phone=order.phone,
        amount=order.amount,
        script=build_script(order),
        attempt_count=0,
        status=JobStatus.PENDING,
    )
    logger.info(
        "order_id=%s name=%s phone=%s amount=%s status=%s attempt_count=%s "
        "max_attempts=%s queued confirmation job",
        job.order_id,
        job.customer_name,
        job.phone,
        job.amount,
        job.status.value,
        job.attempt_count,
        job.max_attempts,
    )
    # TODO: hand to voline for actual Plivo call
    return job
