"""Queue confirmation jobs. Decides that a call should happen, never how it is placed."""

import logging
from enum import Enum

from voroute.models import Order
from voroute.store import (
    DuplicateOrder,
    create_pending,
    ensure_pilot_merchant,
    fetch_job,
    locked_update,
    mark_calling,
)
from voroute.voflow.job import CapturePath, ConfirmationJob, JobStatus
from voroute.voline.provider import place_call

logger = logging.getLogger("voroute.voflow")

_DECISIVE = {JobStatus.CONFIRMED.value, JobStatus.DECLINED.value}


class AfterListen(str, Enum):
    HANGUP = "hangup"
    GATHER = "gather"
    NONE = "none"


def get_job(job_id: str) -> ConfirmationJob | None:
    return fetch_job(job_id)


def enqueue(order: Order, merchant_id: str | None = None) -> ConfirmationJob:
    owner = merchant_id or ensure_pilot_merchant()
    try:
        job = create_pending(owner, order)
    except DuplicateOrder:
        raise
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
    job = mark_calling(job.job_id, call_id)
    logger.info(
        "order_id=%s job_id=%s call_id=%s status=%s confirmation call placed",
        job.order_id,
        job.job_id,
        call_id,
        job.status.value,
    )
    return job


def note_speech(job_id: str, verdict: str, transcript: str) -> None:
    """Store the single speech verdict. The job stays CALLING until keypad or a decisive answer."""

    def _note(job: ConfirmationJob) -> None:
        if job.status is not JobStatus.CALLING or job.speech_result:
            return
        job.speech_result = verdict
        job.transcript = transcript

    locked_update(job_id, _note)


def apply_listen_result(job_id: str) -> AfterListen:
    """Record a decisive speech answer, or offer the keypad. Missing jobs return NONE."""

    found = locked_update(job_id, _decide_listen)
    if found is None:
        return AfterListen.NONE
    return found[0]


def apply_stream_error(job_id: str) -> AfterListen:
    """If the redirect never runs, finish a decisive answer or send the keypad once."""

    found = locked_update(job_id, _decide_stream_error)
    if found is None:
        return AfterListen.NONE
    return found[0]


def apply_digits(job_id: str, digits: str) -> AfterListen:
    """1 confirms, 2 declines, anything else is UNCLEAR. A finished job only hangs up."""

    def _decide(job: ConfirmationJob) -> AfterListen:
        return _decide_digits(job, digits)

    found = locked_update(job_id, _decide)
    if found is None:
        return AfterListen.NONE
    return found[0]


def _decide_listen(job: ConfirmationJob) -> AfterListen:
    if job.status is not JobStatus.CALLING:
        return AfterListen.HANGUP
    if job.speech_result in _DECISIVE:
        job.record_outcome(JobStatus(job.speech_result), CapturePath.SPEECH)
        _log_outcome(job)
        return AfterListen.HANGUP
    job.keypad_offered = True
    logger.info(
        "job_id=%s voice=keypad reason=speech-%s",
        job.job_id,
        job.speech_result or "empty",
    )
    return AfterListen.GATHER


def _decide_stream_error(job: ConfirmationJob) -> AfterListen:
    if job.status is not JobStatus.CALLING:
        return AfterListen.NONE
    if job.speech_result in _DECISIVE:
        job.record_outcome(JobStatus(job.speech_result), CapturePath.SPEECH)
        _log_outcome(job)
        return AfterListen.HANGUP
    if job.keypad_offered:
        return AfterListen.NONE
    job.keypad_offered = True
    logger.info("job_id=%s voice=keypad reason=stream-error", job.job_id)
    return AfterListen.GATHER


def _decide_digits(job: ConfirmationJob, digits: str) -> AfterListen:
    if job.status is not JobStatus.CALLING:
        return AfterListen.HANGUP
    if digits == "1":
        status, path = JobStatus.CONFIRMED, CapturePath.KEYPAD
    elif digits == "2":
        status, path = JobStatus.DECLINED, CapturePath.KEYPAD
    else:
        status, path = JobStatus.UNCLEAR, CapturePath.UNCLEAR
    job.record_outcome(status, path)
    _log_outcome(job)
    return AfterListen.HANGUP


def _log_outcome(job: ConfirmationJob) -> None:
    logger.info(
        "job_id=%s outcome=%s path=%s transcript=%s",
        job.job_id,
        job.status.value,
        job.capture_path,
        job.transcript,
    )
