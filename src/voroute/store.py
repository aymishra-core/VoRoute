"""The only SQL surface. Webhooks load a job by id; orders belong to the pilot merchant."""

import hashlib
from collections.abc import Callable
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from voroute.config import settings
from voroute.models import Order
from voroute.tables import JobRow, MerchantRow, OrderRow, OutcomeRow
from voroute.voflow.job import ConfirmationJob, JobStatus

_ANSWERED = {
    JobStatus.CONFIRMED.value,
    JobStatus.DECLINED.value,
    JobStatus.UNCLEAR.value,
}
PILOT_NAME = "Pilot"


class DuplicateOrder(Exception):
    """This merchant already has a row for that store order id."""


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def ensure_pilot_merchant() -> str:
    """One merchant for the shared VOROUTE_API_KEY. Created the first time the key is set."""

    key = settings.voroute_api_key.strip()
    if not key:
        raise RuntimeError("VOROUTE_API_KEY is not set")
    digest = token_hash(key)
    with _session() as session:
        found = session.scalar(
            select(MerchantRow).where(MerchantRow.api_token_hash == digest)
        )
        if found is not None:
            return found.id
        merchant = MerchantRow(id=str(uuid4()), name=PILOT_NAME, api_token_hash=digest)
        session.add(merchant)
        session.commit()
        return merchant.id


def create_pending(merchant_id: str, order: Order) -> ConfirmationJob:
    job = ConfirmationJob.from_order(order)
    order_row = OrderRow(
        id=str(uuid4()),
        merchant_id=merchant_id,
        external_order_id=order.order_id,
        customer_name=order.customer_name,
        phone=order.phone,
        amount=order.amount,
        cod=order.cod,
    )
    job_row = JobRow(
        id=job.job_id,
        merchant_id=merchant_id,
        order_id=order_row.id,
        script=job.script,
        attempt_count=job.attempt_count,
        max_attempts=job.max_attempts,
        status=job.status.value,
        speech_result="",
        capture_path="",
        transcript="",
        keypad_offered=False,
    )
    with _session() as session:
        session.add(order_row)
        try:
            session.flush()
            session.add(job_row)
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            if "UNIQUE" not in str(exc).upper():
                raise
            raise DuplicateOrder(order.order_id) from exc
    return job


def fetch_job(job_id: str) -> ConfirmationJob | None:
    with _session() as session:
        row = session.get(JobRow, job_id)
        if row is None:
            return None
        order = session.get(OrderRow, row.order_id)
        if order is None:
            return None
        return _to_job(row, order)


def mark_calling(job_id: str, call_sid: str) -> ConfirmationJob:
    def _mark(job: ConfirmationJob) -> None:
        job.transition(JobStatus.CALLING)

    updated = locked_update(job_id, _mark, call_sid=call_sid)
    if updated is None:
        raise RuntimeError(f"job {job_id} disappeared before the call was marked")
    return updated[1]


def locked_update(
    job_id: str,
    mutate: Callable[[ConfirmationJob], object],
    *,
    call_sid: str | None = None,
) -> tuple[object, ConfirmationJob] | None:
    """Lock the job row, run the state machine, and insert at most one outcome."""

    from voroute.db import prepare_database, session_factory

    prepare_database()
    session = session_factory()()
    try:
        with session.begin():
            row = session.scalar(
                select(JobRow).where(JobRow.id == job_id).with_for_update()
            )
            if row is None:
                return None
            order = session.get(OrderRow, row.order_id)
            if order is None:
                return None
            job = _to_job(row, order)
            result = mutate(job)
            _write_job(row, job)
            if call_sid is not None:
                row.call_sid = call_sid
            if job.status.value in _ANSWERED and job.capture_path:
                already = session.scalar(
                    select(OutcomeRow).where(OutcomeRow.job_id == job.job_id)
                )
                if already is None:
                    session.add(
                        OutcomeRow(
                            id=str(uuid4()),
                            merchant_id=row.merchant_id,
                            job_id=row.id,
                            order_id=row.order_id,
                            status=job.status.value,
                            capture_path=job.capture_path,
                            transcript=job.transcript,
                        )
                    )
            return result, job
    finally:
        session.close()


def order_merchant_id(external_order_id: str) -> str | None:
    with _session() as session:
        row = session.scalar(
            select(OrderRow).where(OrderRow.external_order_id == external_order_id)
        )
        if row is None:
            return None
        return row.merchant_id


def outcome_count(job_id: str) -> int:
    with _session() as session:
        rows = session.scalars(
            select(OutcomeRow).where(OutcomeRow.job_id == job_id)
        ).all()
        return len(rows)


def _session() -> Session:
    from voroute.db import prepare_database, session_factory

    prepare_database()
    return session_factory()()


def _to_job(row: JobRow, order: OrderRow) -> ConfirmationJob:
    return ConfirmationJob(
        job_id=row.id,
        order_id=order.external_order_id,
        customer_name=order.customer_name,
        phone=order.phone,
        amount=order.amount,
        script=row.script,
        attempt_count=row.attempt_count,
        max_attempts=row.max_attempts,
        status=JobStatus(row.status),
        speech_result=row.speech_result,
        capture_path=row.capture_path,
        transcript=row.transcript,
        keypad_offered=row.keypad_offered,
    )


def _write_job(row: JobRow, job: ConfirmationJob) -> None:
    row.status = job.status.value
    row.attempt_count = job.attempt_count
    row.max_attempts = job.max_attempts
    row.script = job.script
    row.speech_result = job.speech_result
    row.capture_path = job.capture_path
    row.transcript = job.transcript
    row.keypad_offered = job.keypad_offered
