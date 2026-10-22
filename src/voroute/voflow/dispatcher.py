"""Queue confirmation jobs. Decides that a call should happen, never how it is placed."""

import logging
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import NamedTuple

from voroute.models import Order
from voroute.store import (
    DuplicateOrder,
    create_pending,
    due_job_ids,
    ensure_pilot_merchant,
    fetch_job,
    locked_update,
    mark_calling,
    mark_place_failed,
    stuck_calling_ids,
)
from voroute.voflow.job import CapturePath, ConfirmationJob, JobStatus
from voroute.voline.provider import place_call

logger = logging.getLogger("voroute.voflow")


class PlaceFailed(Exception):
    """The first dial was rejected before the customer was reached."""


class Sweep(NamedTuple):
    due: int
    dialed: int
    cutoff: int

_DECISIVE = {JobStatus.CONFIRMED.value, JobStatus.DECLINED.value}
_CALL_ENDED = {"completed", "busy", "no-answer", "failed", "canceled"}
_RETRY = {"no-answer", "busy", "failed"}
_TERMINAL = {"completed", "canceled"}
_FAILED_ATTEMPT_CAP = 2
_RETRY_DELAYS = {1: timedelta(hours=2), 2: timedelta(hours=3)}
_CUTOFF = timedelta(hours=6)
_STUCK_CALLING = timedelta(minutes=2)


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
    try:
        call_id = place_call(order.phone, job)
    except Exception:
        logger.exception("job_id=%s voice=place-failed", job.job_id)
        mark_place_failed(job.job_id)
        raise PlaceFailed() from None
    job = mark_calling(job.job_id, call_id)
    logger.info(
        "order_id=%s job_id=%s call_id=%s status=%s confirmation call placed",
        job.order_id,
        job.job_id,
        call_id,
        job.status.value,
    )
    dispatch_due()
    return fetch_job(job.job_id) or job


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


def apply_call_ended(
    job_id: str,
    call_status: str,
    call_sid: str = "",
    now: datetime | None = None,
) -> None:
    """Settle or schedule from a terminal Twilio status. A recorded answer is left as it is."""

    status = call_status.strip()
    if status not in _CALL_ENDED:
        return
    moment = _clock(now)

    def _end(job: ConfirmationJob) -> None:
        if job.status is not JobStatus.CALLING:
            return
        if not job.call_sid or job.call_sid != call_sid:
            return
        _finish_attempt(job, status, moment)

    locked_update(job_id, _end)


def dispatch_due(now: datetime | None = None) -> Sweep:
    """Place retries whose due time has arrived. Claim the row before dialing."""

    moment = _clock(now)
    due = due_job_ids(moment)
    dialed = 0
    cutoff = 0
    for job_id in due:
        try:
            outcome = _dial_due(job_id, moment)
        except Exception:
            logger.exception("job_id=%s retry dispatch failed", job_id)
            continue
        if outcome == "dialed":
            dialed += 1
        elif outcome == "cutoff":
            cutoff += 1
    # A claim is only stuck when the row has had no call id for two real minutes.
    # The due-time clock can be ahead of that, and must not recover an in-flight dial.
    wall = _clock(None)
    for job_id in stuck_calling_ids(wall - _STUCK_CALLING):
        try:
            _recover_stuck(job_id, wall)
        except Exception:
            logger.exception("job_id=%s stuck calling recovery failed", job_id)
    return Sweep(due=len(due), dialed=dialed, cutoff=cutoff)


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


def _finish_attempt(job: ConfirmationJob, twilio_status: str, moment: datetime) -> None:
    if job.status is not JobStatus.CALLING:
        return
    if twilio_status in _TERMINAL:
        _settle_unclear(job)
        return
    if twilio_status not in _RETRY:
        return
    next_count = job.attempt_count + 1
    cap = _FAILED_ATTEMPT_CAP if twilio_status == "failed" else job.max_attempts
    delay = _RETRY_DELAYS.get(next_count)
    ordered = job.ordered_at
    if delay is not None and ordered is not None and next_count < cap:
        due = moment + delay
        if due <= ordered + _CUTOFF:
            job.defer_retry(due)
            logger.info(
                "job_id=%s retry scheduled attempt=%s of %s due=%s twilio=%s",
                job.job_id,
                job.attempt_count + 1,
                job.max_attempts,
                due.isoformat(),
                twilio_status,
            )
            return
    job.attempt_count = next_count
    _settle_unclear(job)


def _settle_unclear(job: ConfirmationJob) -> None:
    job.next_attempt_at = None
    job.record_outcome(JobStatus.UNCLEAR, CapturePath.UNCLEAR)
    _log_outcome(job)


def _dial_due(job_id: str, moment: datetime) -> str:
    def _claim(job: ConfirmationJob) -> str:
        if job.status is not JobStatus.PENDING or job.next_attempt_at is None:
            return "skip"
        if job.next_attempt_at > moment:
            return "skip"
        if job.ordered_at is None or moment > job.ordered_at + _CUTOFF:
            job.transition(JobStatus.CALLING)
            job.next_attempt_at = None
            _settle_unclear(job)
            return "cutoff"
        job.transition(JobStatus.CALLING)
        job.next_attempt_at = None
        job.call_sid = ""
        return "dial"

    found = locked_update(job_id, _claim)
    if found is None or found[0] != "dial":
        return "skip" if found is None else found[0]
    job = found[1]
    try:
        sid = place_call(job.phone, job)
    except Exception:
        logger.exception("job_id=%s retry dial failed", job.job_id)
        locked_update(
            job_id,
            lambda current: _finish_attempt(current, "failed", moment)
            if current.status is JobStatus.CALLING and not current.call_sid
            else None,
        )
        return "skip"

    def _save(current: ConfirmationJob) -> None:
        if current.status is JobStatus.CALLING and not current.call_sid:
            current.call_sid = sid

    locked_update(job_id, _save)
    logger.info("job_id=%s call_id=%s retry dial placed", job_id, sid)
    return "dialed"


def _recover_stuck(job_id: str, moment: datetime) -> None:
    def _recover(job: ConfirmationJob) -> None:
        if job.status is not JobStatus.CALLING or job.call_sid:
            return
        updated = job.updated_at
        if updated is None or updated > moment - _STUCK_CALLING:
            return
        _finish_attempt(job, "failed", moment)

    locked_update(job_id, _recover)


def _clock(now: datetime | None) -> datetime:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _log_outcome(job: ConfirmationJob) -> None:
    logger.info(
        "job_id=%s outcome=%s path=%s transcript=%s",
        job.job_id,
        job.status.value,
        job.capture_path,
        job.transcript,
    )
