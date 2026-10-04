"""Confirmation job and the status machine that decides when a call may be retried."""

from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field

from voroute.models import Order
from voroute.voflow.script import build_script


class JobStatus(str, Enum):
    PENDING = "PENDING"
    CALLING = "CALLING"
    CONFIRMED = "CONFIRMED"
    DECLINED = "DECLINED"
    FAILED = "FAILED"
    MAX_RETRIES = "MAX_RETRIES"


_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.PENDING: {JobStatus.CALLING},
    JobStatus.CALLING: {
        JobStatus.CONFIRMED,
        JobStatus.DECLINED,
        JobStatus.FAILED,
        JobStatus.MAX_RETRIES,
    },
    JobStatus.FAILED: {JobStatus.PENDING},
    JobStatus.CONFIRMED: set(),
    JobStatus.DECLINED: set(),
    JobStatus.MAX_RETRIES: set(),
}


class InvalidJobTransition(Exception):
    """Raised when a job is moved along an edge the status machine does not allow."""


class ConfirmationJob(BaseModel):
    """Everything a confirmation call needs, except how to place it."""

    job_id: str = Field(default_factory=lambda: str(uuid4()))
    order_id: str
    customer_name: str
    phone: str
    amount: float
    script: str
    attempt_count: int = 0
    max_attempts: int = 3
    status: JobStatus = JobStatus.PENDING

    @classmethod
    def from_order(cls, order: Order) -> "ConfirmationJob":
        return cls(
            order_id=order.order_id,
            customer_name=order.customer_name,
            phone=order.phone,
            amount=order.amount,
            script=build_script(order),
        )

    def transition(self, status: JobStatus) -> None:
        allowed = _TRANSITIONS[self.status]
        if status not in allowed:
            raise InvalidJobTransition(
                f"cannot move {self.status.value} → {status.value}"
            )
        self.status = status

    def record_failure(self) -> None:
        """Count a failed attempt. Under the cap the job can be retried; at the cap it stops."""

        if self.status is not JobStatus.CALLING:
            raise InvalidJobTransition(
                f"cannot record a failure from {self.status.value}"
            )
        next_attempt = self.attempt_count + 1
        outcome = (
            JobStatus.MAX_RETRIES
            if next_attempt >= self.max_attempts
            else JobStatus.FAILED
        )
        self.transition(outcome)
        self.attempt_count = next_attempt

    def queue_retry(self) -> None:
        """Requeue a failed job. Refuses once attempt_count has reached max_attempts."""

        if self.attempt_count >= self.max_attempts:
            raise InvalidJobTransition(
                f"attempt_count={self.attempt_count} has reached "
                f"max_attempts={self.max_attempts}"
            )
        self.transition(JobStatus.PENDING)
