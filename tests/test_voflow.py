import pytest

from voroute.models import Order
from voroute.voflow import (
    ConfirmationJob,
    InvalidJobTransition,
    JobStatus,
    build_script,
    enqueue,
)

def _order(
    *,
    customer_name: str = "Asha",
    amount: float = 499.0,
) -> Order:
    return Order(
        order_id="ORD-1001",
        customer_name=customer_name,
        phone="+919876543210",
        amount=amount,
        cod=True,
    )


def test_enqueue_produces_pending_job() -> None:
    job = enqueue(_order())

    assert job.status is JobStatus.PENDING
    assert job.order_id == "ORD-1001"
    assert job.customer_name == "Asha"
    assert job.phone == "+919876543210"
    assert job.amount == 499.0
    assert job.attempt_count == 0
    assert job.max_attempts == 3
    assert job.script == (
        "Namaste Asha ji, aapka ₹499 ka COD order confirm karna tha. "
        "Order chahiye? Haan ya na?"
    )


def test_script_builder_outputs_name_and_amount() -> None:
    script = build_script(_order(customer_name="Ravi", amount=1299.5))

    assert script == (
        "Namaste Ravi ji, aapka ₹1299.50 ka COD order confirm karna tha. "
        "Order chahiye? Haan ya na?"
    )


@pytest.mark.parametrize(
    "outcome",
    [
        JobStatus.CONFIRMED,
        JobStatus.DECLINED,
        JobStatus.FAILED,
        JobStatus.MAX_RETRIES,
    ],
)
def test_status_transitions_follow_the_call_flow(outcome: JobStatus) -> None:
    job = enqueue(_order())

    job.transition(JobStatus.CALLING)
    job.transition(outcome)

    assert job.status is outcome


def test_illegal_status_transition_is_rejected() -> None:
    job = enqueue(_order())

    with pytest.raises(InvalidJobTransition):
        job.transition(JobStatus.CONFIRMED)

    job.transition(JobStatus.CALLING)
    job.transition(JobStatus.CONFIRMED)
    with pytest.raises(InvalidJobTransition):
        job.transition(JobStatus.CALLING)


def test_failed_attempt_retries_until_max() -> None:
    job = enqueue(_order())

    for _ in range(2):
        job.transition(JobStatus.CALLING)
        job.record_failure()
        assert job.status is JobStatus.FAILED
        job.queue_retry()
        assert job.status is JobStatus.PENDING

    job.transition(JobStatus.CALLING)
    job.record_failure()

    assert job.attempt_count == 3
    assert job.status is JobStatus.MAX_RETRIES
    with pytest.raises(InvalidJobTransition):
        job.queue_retry()


def test_max_attempts_defaults_to_3() -> None:
    job = ConfirmationJob(
        order_id="ORD-1001",
        customer_name="Asha",
        phone="+919876543210",
        amount=499.0,
        script="Namaste Asha ji",
    )

    assert job.max_attempts == 3
    assert job.attempt_count == 0
    assert job.status is JobStatus.PENDING
