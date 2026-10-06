import pytest

from voroute.models import Order
from voroute.voflow import (
    CapturePath,
    ConfirmationJob,
    InvalidJobTransition,
    JobStatus,
    build_closing,
    build_script,
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
    job = ConfirmationJob.from_order(_order())

    assert job.status is JobStatus.PENDING
    assert job.order_id == "ORD-1001"
    assert job.customer_name == "Asha"
    assert job.phone == "+919876543210"
    assert job.amount == 499.0
    assert job.attempt_count == 0
    assert job.max_attempts == 3
    assert job.script == (
        "Namaste Asha ji. Aapka ₹499 ka cash on delivery order confirm "
        "karne ke liye call kiya hai. Kya aap yeh order confirm karna "
        "chahenge? Haan ya na."
    )


def test_script_builder_outputs_name_and_amount() -> None:
    script = build_script(_order(customer_name="Ravi", amount=1299.5))

    assert script == (
        "Namaste Ravi ji. Aapka ₹1299.50 ka cash on delivery order confirm "
        "karne ke liye call kiya hai. Kya aap yeh order confirm karna "
        "chahenge? Haan ya na."
    )


def test_closing_lines_match_the_outcome() -> None:
    assert build_closing("Asha", "CONFIRMED") == (
        "Dhanyawaad Asha ji, aapka order confirm kar diya gaya hai."
    )
    assert build_closing("Asha", "DECLINED") == (
        "Theek hai Asha ji, aapka order cancel kar diya gaya hai. Dhanyawaad."
    )
    unclear = build_closing("Asha", "UNCLEAR")
    assert unclear == "Dhanyawaad, hum aapse dobara sampark karenge."
    assert "confirm" not in unclear
    assert "cancel" not in unclear


@pytest.mark.parametrize(
    "outcome",
    [
        JobStatus.CONFIRMED,
        JobStatus.DECLINED,
        JobStatus.UNCLEAR,
        JobStatus.FAILED,
        JobStatus.MAX_RETRIES,
    ],
)
def test_status_transitions_follow_the_call_flow(outcome: JobStatus) -> None:
    job = ConfirmationJob.from_order(_order())

    job.transition(JobStatus.CALLING)
    job.transition(outcome)

    assert job.status is outcome


def test_illegal_status_transition_is_rejected() -> None:
    job = ConfirmationJob.from_order(_order())

    with pytest.raises(InvalidJobTransition):
        job.transition(JobStatus.CONFIRMED)

    job.transition(JobStatus.CALLING)
    job.transition(JobStatus.CONFIRMED)
    with pytest.raises(InvalidJobTransition):
        job.transition(JobStatus.CALLING)
    with pytest.raises(InvalidJobTransition):
        job.transition(JobStatus.UNCLEAR)


def test_failed_attempt_retries_until_max() -> None:
    job = ConfirmationJob.from_order(_order())

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


def test_unclear_is_terminal_and_does_not_retry() -> None:
    job = ConfirmationJob.from_order(_order())

    with pytest.raises(InvalidJobTransition):
        job.record_outcome(JobStatus.UNCLEAR, CapturePath.UNCLEAR)

    job.transition(JobStatus.CALLING)
    job.record_outcome(JobStatus.UNCLEAR, CapturePath.UNCLEAR)

    assert job.status is JobStatus.UNCLEAR
    assert job.capture_path == "unclear"
    assert job.attempt_count == 0
    with pytest.raises(InvalidJobTransition):
        job.record_outcome(JobStatus.CONFIRMED, CapturePath.SPEECH)
    with pytest.raises(InvalidJobTransition):
        job.queue_retry()


def test_record_outcome_rejects_a_non_answer() -> None:
    job = ConfirmationJob.from_order(_order())
    job.transition(JobStatus.CALLING)

    with pytest.raises(InvalidJobTransition):
        job.record_outcome(JobStatus.FAILED, CapturePath.SPEECH)


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
