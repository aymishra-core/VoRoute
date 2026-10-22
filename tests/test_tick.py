"""POST /tasks/tick wakes the retry sweep. It never accepts a merchant token."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from voroute.config import settings
from voroute.main import app
from voroute.models import Order
from voroute.voflow import enqueue
from voroute.voflow.dispatcher import apply_call_ended, get_job
from voroute.voflow.job import ConfirmationJob
from voroute.voline.provider import set_provider

TICK = "tick-secret-value"
_NOT_CONFIGURED = {"detail": "tick not configured: TICK_TOKEN is not set"}
_UNAUTHORIZED = {"detail": "unauthorized"}

client = TestClient(app)


class _Dials:
    def __init__(self) -> None:
        self.sids: list[str] = []

    def place_call(self, to: str, job: ConfirmationJob) -> str:
        sid = f"CA{len(self.sids) + 1}"
        self.sids.append(sid)
        return sid


def _schedule(dials: _Dials, order_id: str) -> tuple[str, datetime]:
    set_provider(dials)
    job = enqueue(
        Order(
            order_id=order_id,
            customer_name="Asha",
            phone="+919876543210",
            amount=499.0,
            cod=True,
        )
    )
    stored = get_job(job.job_id)
    assert stored is not None and stored.ordered_at is not None
    apply_call_ended(job.job_id, "no-answer", stored.call_sid, now=stored.ordered_at)
    return job.job_id, stored.ordered_at


def _freeze(monkeypatch: pytest.MonkeyPatch, moment: datetime) -> None:
    def _clock(now: datetime | None = None) -> datetime:
        if now is None:
            return moment
        if now.tzinfo is None:
            return now.replace(tzinfo=timezone.utc)
        return now.astimezone(timezone.utc)

    monkeypatch.setattr("voroute.voflow.dispatcher._clock", _clock)


def _tick(token: str | None) -> object:
    headers = {} if token is None else {"Authorization": f"Bearer {token}"}
    return client.post("/tasks/tick", headers=headers)


@pytest.mark.parametrize(
    "token",
    [None, "wrong-token", "test-key"],
)
def test_tick_without_the_tick_token_does_no_work(
    monkeypatch: pytest.MonkeyPatch, token: str | None
) -> None:
    monkeypatch.setattr(settings, "tick_token", TICK)
    dials = _Dials()
    job_id, t0 = _schedule(dials, f"ORD-tick-401-{token}")
    _freeze(monkeypatch, t0 + timedelta(hours=2))

    response = _tick(token)

    assert response.status_code == 401
    assert response.json() == _UNAUTHORIZED
    assert dials.sids == ["CA1"]
    stored = get_job(job_id)
    assert stored is not None
    assert stored.status.value == "PENDING"


def test_tick_without_a_configured_token_is_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "tick_token", "")
    dials = _Dials()
    job_id, t0 = _schedule(dials, "ORD-tick-503")
    _freeze(monkeypatch, t0 + timedelta(hours=2))

    response = _tick(TICK)

    assert response.status_code == 503
    assert response.json() == _NOT_CONFIGURED
    assert "TICK_TOKEN" in response.text
    assert dials.sids == ["CA1"]
    stored = get_job(job_id)
    assert stored is not None
    assert stored.status.value == "PENDING"


def test_valid_tick_dials_a_due_job_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "tick_token", TICK)
    dials = _Dials()
    job_id, t0 = _schedule(dials, "ORD-tick-due")
    _freeze(monkeypatch, t0 + timedelta(hours=2))

    first = _tick(TICK)
    assert first.status_code == 200
    assert first.json() == {"due": 1, "dialed": 1, "cutoff": 0}
    assert set(first.json()) == {"due", "dialed", "cutoff"}
    assert dials.sids == ["CA1", "CA2"]
    assert job_id not in first.text
    assert "+919876543210" not in first.text

    second = _tick(TICK)
    assert second.status_code == 200
    assert second.json() == {"due": 0, "dialed": 0, "cutoff": 0}
    assert dials.sids == ["CA1", "CA2"]


def test_tick_past_the_cutoff_settles_unclear(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "tick_token", TICK)
    dials = _Dials()
    job_id, t0 = _schedule(dials, "ORD-tick-cutoff")
    _freeze(monkeypatch, t0 + timedelta(hours=7))

    response = _tick(TICK)

    assert response.status_code == 200
    assert response.json() == {"due": 1, "dialed": 0, "cutoff": 1}
    assert dials.sids == ["CA1"]
    stored = get_job(job_id)
    assert stored is not None
    assert stored.status.value == "UNCLEAR"
