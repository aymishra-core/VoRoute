"""Retries are scheduled from a miss, then dialed only when the due time arrives."""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import update

from voroute.db import session_factory
from voroute.main import app
from voroute.models import Order
from voroute.store import outcome_count
from voroute.tables import JobRow
from voroute.voflow import enqueue
from voroute.voflow.dispatcher import (
    apply_call_ended,
    apply_listen_result,
    dispatch_due,
    get_job,
    note_speech,
)
from voroute.voflow.job import ConfirmationJob, JobStatus
from voroute.voline.provider import set_provider

_TWO_HOURS = timedelta(hours=2)
_THREE_HOURS = timedelta(hours=3)


class _Dials:
    def __init__(self) -> None:
        self.sids: list[str] = []
        self.reenter_at: datetime | None = None
        self.fail_after = 0

    def place_call(self, to: str, job: ConfirmationJob) -> str:
        n = len(self.sids) + 1
        if self.fail_after and n > self.fail_after:
            raise RuntimeError("carrier down")
        sid = f"CA{n}"
        self.sids.append(sid)
        if self.reenter_at is not None and n == 2:
            due = self.reenter_at
            self.reenter_at = None
            dispatch_due(due)
        return sid


def _order(order_id: str) -> Order:
    return Order(
        order_id=order_id,
        customer_name="Asha",
        phone="+919876543210",
        amount=499.0,
        cod=True,
    )


def _start(dials: _Dials, order_id: str) -> tuple[ConfirmationJob, datetime]:
    set_provider(dials)
    job = enqueue(_order(order_id))
    stored = get_job(job.job_id)
    assert stored is not None
    assert stored.ordered_at is not None
    assert stored.call_sid == "CA1"
    assert stored.status is JobStatus.CALLING
    return stored, stored.ordered_at


def _end(job_id: str, status: str, sid: str, moment: datetime) -> None:
    apply_call_ended(job_id, status, sid, now=moment)


def test_no_answer_and_busy_schedule_the_next_attempt_and_stop_at_three() -> None:
    for status in ("no-answer", "busy"):
        dials = _Dials()
        job, t0 = _start(dials, f"ORD-{status}")
        note_speech(job.job_id, "UNCLEAR", "hmm")

        _end(job.job_id, status, "CA1", t0)
        _end(job.job_id, status, "CA1", t0)

        waiting = get_job(job.job_id)
        assert waiting is not None
        assert waiting.status is JobStatus.PENDING
        assert waiting.status is not JobStatus.CONFIRMED
        assert waiting.attempt_count == 1
        assert waiting.next_attempt_at == t0 + _TWO_HOURS
        assert waiting.speech_result == ""
        assert waiting.transcript == ""
        assert waiting.keypad_offered is False
        assert outcome_count(job.job_id) == 0
        assert dials.sids == ["CA1"]

        dispatch_due(t0 + _TWO_HOURS - timedelta(seconds=1))
        assert dials.sids == ["CA1"]

        dispatch_due(t0 + _TWO_HOURS)
        second = get_job(job.job_id)
        assert second is not None
        assert second.status is JobStatus.CALLING
        assert second.call_sid == "CA2"
        assert dials.sids == ["CA1", "CA2"]

        _end(job.job_id, status, "CA2", t0 + _TWO_HOURS)
        waiting = get_job(job.job_id)
        assert waiting is not None
        assert waiting.attempt_count == 2
        assert waiting.next_attempt_at == t0 + _TWO_HOURS + _THREE_HOURS

        dispatch_due(t0 + _TWO_HOURS + _THREE_HOURS)
        assert dials.sids == ["CA1", "CA2", "CA3"]
        _end(job.job_id, status, "CA3", t0 + _TWO_HOURS + _THREE_HOURS)

        done = get_job(job.job_id)
        assert done is not None
        assert done.status is JobStatus.UNCLEAR
        assert done.status is not JobStatus.CONFIRMED
        assert done.attempt_count == 3
        assert done.next_attempt_at is None
        assert done.capture_path == "unclear"
        assert outcome_count(job.job_id) == 1
        dispatch_due(t0 + timedelta(hours=5, minutes=30))
        assert dials.sids == ["CA1", "CA2", "CA3"]


def test_a_second_sweep_does_not_dial_twice() -> None:
    dials = _Dials()
    job, t0 = _start(dials, "ORD-once")
    due = t0 + _TWO_HOURS
    dials.reenter_at = due
    _end(job.job_id, "no-answer", "CA1", t0)

    dispatch_due(due)

    assert dials.sids == ["CA1", "CA2"]
    stored = get_job(job.job_id)
    assert stored is not None
    assert stored.status is JobStatus.CALLING
    assert stored.call_sid == "CA2"


def test_failed_retries_once_then_settles_unclear() -> None:
    dials = _Dials()
    job, t0 = _start(dials, "ORD-failed")
    _end(job.job_id, "failed", "CA1", t0)

    waiting = get_job(job.job_id)
    assert waiting is not None
    assert waiting.status is JobStatus.PENDING
    assert waiting.attempt_count == 1
    assert waiting.next_attempt_at == t0 + _TWO_HOURS

    dispatch_due(t0 + _TWO_HOURS)
    _end(job.job_id, "failed", "CA2", t0 + _TWO_HOURS)

    done = get_job(job.job_id)
    assert done is not None
    assert done.status is JobStatus.UNCLEAR
    assert done.status is not JobStatus.CONFIRMED
    assert done.attempt_count == 2
    assert done.next_attempt_at is None
    assert outcome_count(job.job_id) == 1
    assert dials.sids == ["CA1", "CA2"]


def test_no_answer_then_failed_does_not_take_a_third_attempt() -> None:
    dials = _Dials()
    job, t0 = _start(dials, "ORD-mixed")
    _end(job.job_id, "no-answer", "CA1", t0)
    dispatch_due(t0 + _TWO_HOURS)
    _end(job.job_id, "failed", "CA2", t0 + _TWO_HOURS)

    done = get_job(job.job_id)
    assert done is not None
    assert done.status is JobStatus.UNCLEAR
    assert done.attempt_count == 2
    assert dials.sids == ["CA1", "CA2"]


def test_failed_then_no_answer_can_still_use_the_third_attempt() -> None:
    dials = _Dials()
    job, t0 = _start(dials, "ORD-mixed-2")
    _end(job.job_id, "failed", "CA1", t0)
    dispatch_due(t0 + _TWO_HOURS)
    _end(job.job_id, "no-answer", "CA2", t0 + _TWO_HOURS)

    waiting = get_job(job.job_id)
    assert waiting is not None
    assert waiting.status is JobStatus.PENDING
    assert waiting.attempt_count == 2
    dispatch_due(t0 + _TWO_HOURS + _THREE_HOURS)
    _end(job.job_id, "no-answer", "CA3", t0 + _TWO_HOURS + _THREE_HOURS)

    done = get_job(job.job_id)
    assert done is not None
    assert done.status is JobStatus.UNCLEAR
    assert done.attempt_count == 3
    assert dials.sids == ["CA1", "CA2", "CA3"]


def test_a_recorded_answer_is_never_retried() -> None:
    for verdict in ("CONFIRMED", "DECLINED"):
        dials = _Dials()
        job, t0 = _start(dials, f"ORD-{verdict}")
        note_speech(job.job_id, verdict, "haan" if verdict == "CONFIRMED" else "nahi")
        apply_listen_result(job.job_id)

        _end(job.job_id, "no-answer", "CA1", t0)
        dispatch_due(t0 + _TWO_HOURS)

        stored = get_job(job.job_id)
        assert stored is not None
        assert stored.status is JobStatus(verdict)
        assert stored.next_attempt_at is None
        assert stored.attempt_count == 0
        assert dials.sids == ["CA1"]


def test_canceled_and_completed_while_calling_are_terminal_unclear() -> None:
    for status in ("canceled", "completed"):
        dials = _Dials()
        job, t0 = _start(dials, f"ORD-term-{status}")
        _end(job.job_id, status, "CA1", t0)
        dispatch_due(t0 + _TWO_HOURS)

        stored = get_job(job.job_id)
        assert stored is not None
        assert stored.status is JobStatus.UNCLEAR
        assert stored.status is not JobStatus.CONFIRMED
        assert stored.capture_path == "unclear"
        assert stored.attempt_count == 0
        assert stored.next_attempt_at is None
        assert outcome_count(job.job_id) == 1
        assert dials.sids == ["CA1"]


def test_a_different_call_sid_does_not_schedule_a_retry() -> None:
    dials = _Dials()
    job, t0 = _start(dials, "ORD-sid")
    _end(job.job_id, "no-answer", "CA_OTHER", t0)

    stored = get_job(job.job_id)
    assert stored is not None
    assert stored.status is JobStatus.CALLING
    assert stored.attempt_count == 0
    assert stored.next_attempt_at is None
    assert dials.sids == ["CA1"]


def test_a_miss_inside_the_last_hour_of_the_cutoff_settles_unclear() -> None:
    dials = _Dials()
    job, t0 = _start(dials, "ORD-cutoff")
    _end(job.job_id, "no-answer", "CA1", t0 + timedelta(hours=5))

    stored = get_job(job.job_id)
    assert stored is not None
    assert stored.status is JobStatus.UNCLEAR
    assert stored.status is not JobStatus.CONFIRMED
    assert stored.next_attempt_at is None
    assert outcome_count(job.job_id) == 1
    assert dials.sids == ["CA1"]


def test_a_due_retry_past_the_cutoff_does_not_dial() -> None:
    dials = _Dials()
    job, t0 = _start(dials, "ORD-late")
    _end(job.job_id, "no-answer", "CA1", t0)
    dispatch_due(t0 + timedelta(hours=7))

    stored = get_job(job.job_id)
    assert stored is not None
    assert stored.status is JobStatus.UNCLEAR
    assert stored.status is not JobStatus.CONFIRMED
    assert stored.next_attempt_at is None
    assert dials.sids == ["CA1"]


def test_a_failed_redial_settles_instead_of_staying_calling() -> None:
    dials = _Dials()
    dials.fail_after = 1
    job, t0 = _start(dials, "ORD-redial")
    _end(job.job_id, "no-answer", "CA1", t0)
    dispatch_due(t0 + _TWO_HOURS)

    stored = get_job(job.job_id)
    assert stored is not None
    assert stored.status is JobStatus.UNCLEAR
    assert stored.status is not JobStatus.CONFIRMED
    assert dials.sids == ["CA1"]


def test_a_calling_row_with_no_sid_is_recovered_as_a_technical_failure() -> None:
    dials = _Dials()
    job, _t0 = _start(dials, "ORD-stuck")
    session = session_factory()()
    session.execute(
        update(JobRow)
        .where(JobRow.id == job.job_id)
        .values(
            call_sid=None,
            updated_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
    )
    session.commit()
    session.close()

    dispatch_due(datetime.now(timezone.utc))

    stored = get_job(job.job_id)
    assert stored is not None
    assert stored.status is JobStatus.PENDING
    assert stored.attempt_count == 1
    assert stored.next_attempt_at is not None
    assert stored.status is not JobStatus.CONFIRMED


def test_http_call_status_uses_the_posted_sid() -> None:
    dials = _Dials()
    job, _t0 = _start(dials, "ORD-http")
    client = TestClient(app)
    response = client.post(
        f"/voice/call-status/{job.job_id}",
        data={"CallStatus": "no-answer", "CallSid": "CA_OTHER"},
    )
    assert response.status_code == 204
    stored = get_job(job.job_id)
    assert stored is not None
    assert stored.status is JobStatus.CALLING
    assert stored.next_attempt_at is None
