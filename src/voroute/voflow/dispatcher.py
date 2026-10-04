"""Queue confirmation jobs. Decides that a call should happen, never how it is placed."""

import logging

from voroute.models import Order
from voroute.voflow.job import ConfirmationJob, JobStatus
from voroute.voline.provider import place_call

logger = logging.getLogger("voroute.voflow")

_jobs: dict[str, ConfirmationJob] = {}


def get_job(job_id: str) -> ConfirmationJob | None:
    return _jobs.get(job_id)


def enqueue(order: Order) -> ConfirmationJob:
    job = ConfirmationJob.from_order(order)
    _jobs[job.job_id] = job
    logger.info(
        "order_id=%s job_id=%s name=%s phone=%s amount=%s status=%s "
        "attempt_count=%s max_attempts=%s queued confirmation job",
        job.order_id,
        job.job_id,
        job.customer_name,
        job.phone,
        job.amount,
        job.status.value,
        job.attempt_count,
        job.max_attempts,
    )
    call_id = place_call(order.phone, job)
    job.transition(JobStatus.CALLING)
    logger.info(
        "order_id=%s job_id=%s call_id=%s status=%s confirmation call placed",
        job.order_id,
        job.job_id,
        call_id,
        job.status.value,
    )
    return job
