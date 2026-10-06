"""Queue confirmation jobs. Decides that a call should happen, never how it is placed."""

import logging
import threading
from enum import Enum

from voroute.models import Order
from voroute.voflow.job import CapturePath, ConfirmationJob, JobStatus
from voroute.voline.provider import place_call

logger = logging.getLogger("voroute.voflow")

_jobs: dict[str, ConfirmationJob] = {}
_lock = threading.Lock()
_DECISIVE = {JobStatus.CONFIRMED.value, JobStatus.DECLINED.value}


class AfterListen(str, Enum):
    HANGUP = "hangup"
    GATHER = "gather"
    NONE = "none"


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


def note_speech(job_id: str, verdict: str, transcript: str) -> None:
    """Store the single speech verdict. The job stays CALLING until keypad or a decisive answer."""

    with _lock:
        job = _jobs.get(job_id)
        if job is None or job.status is not JobStatus.CALLING or job.speech_result:
            return
        job.speech_result = verdict
        job.transcript = transcript


def apply_listen_result(job_id: str) -> AfterListen:
    """Record a decisive speech answer, or offer the keypad. Missing jobs return NONE."""

    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return AfterListen.NONE
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


def apply_stream_error(job_id: str) -> AfterListen:
    """If the redirect never runs, finish a decisive answer or send the keypad once."""

    with _lock:
        job = _jobs.get(job_id)
        if job is None or job.status is not JobStatus.CALLING:
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


def apply_digits(job_id: str, digits: str) -> AfterListen:
    """1 confirms, 2 declines, anything else is UNCLEAR. A finished job only hangs up."""

    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return AfterListen.NONE
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
