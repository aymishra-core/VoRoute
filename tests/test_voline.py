import xml.etree.ElementTree as ET
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from voroute.config import settings
from voroute.main import app
from voroute.models import Order
from voroute.voflow import ConfirmationJob, JobStatus, enqueue
from voroute.voflow.dispatcher import get_job
from voroute.voline.provider import TwilioProvider, set_provider

client = TestClient(app)


def _order() -> Order:
    return Order(
        order_id="ORD-1001",
        customer_name="Asha",
        phone="+919876543210",
        amount=499.0,
        cod=True,
    )


def test_twilio_client_is_lazy(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[object] = []

    class FakeClient:
        def __init__(self, *args: object) -> None:
            created.append(args)

    monkeypatch.setattr("twilio.rest.Client", FakeClient)
    TwilioProvider()

    assert created == []


def test_place_call_builds_twilio_params(monkeypatch: pytest.MonkeyPatch) -> None:
    twilio = MagicMock()
    twilio.calls.create.return_value.sid = "CA123"
    monkeypatch.setattr(settings, "twilio_from_number", "+15550001111")
    monkeypatch.setattr(settings, "public_base_url", "https://calls.test/")
    job = ConfirmationJob.from_order(_order())

    call_id = TwilioProvider(client=twilio).place_call(job.phone, job)

    assert call_id == "CA123"
    twilio.calls.create.assert_called_once_with(
        to="+919876543210",
        from_="+15550001111",
        url=f"https://calls.test/voice/twiml/{job.job_id}",
    )


def test_enqueue_moves_job_from_pending_to_calling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    twilio = MagicMock()
    twilio.calls.create.return_value.sid = "CA456"
    monkeypatch.setattr(settings, "twilio_from_number", "+15550001111")
    monkeypatch.setattr(settings, "public_base_url", "https://calls.test")
    statuses: list[JobStatus] = []

    def create(**kwargs: object) -> MagicMock:
        found = get_job(str(kwargs["url"]).rsplit("/", 1)[-1])
        assert found is not None
        statuses.append(found.status)
        return twilio.calls.create.return_value

    twilio.calls.create.side_effect = create
    set_provider(TwilioProvider(client=twilio))

    job = enqueue(_order())

    assert statuses == [JobStatus.PENDING]
    assert job.status is JobStatus.CALLING
    twilio.calls.create.assert_called_once_with(
        to="+919876543210",
        from_="+15550001111",
        url=f"https://calls.test/voice/twiml/{job.job_id}",
    )


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_twiml_endpoint_speaks_the_hinglish_script(method: str) -> None:
    job = enqueue(_order())

    response = client.request(method, f"/voice/twiml/{job.job_id}")

    assert response.status_code == 200
    assert "xml" in response.headers["content-type"]
    root = ET.fromstring(response.text)
    say = root.find("Say")
    assert say is not None
    assert say.attrib["voice"] == "Polly.Aditi"
    assert say.attrib["language"] == "hi-IN"
    assert say.text == job.script
    assert "Namaste Asha ji" in response.text
    assert "₹499" in response.text
    assert root.find("Hangup") is not None


# SUBSTAGE-2-LIVE: set .env, run ngrok, POST an order to a verified number.
@pytest.mark.skip(
    reason="SUBSTAGE-2-LIVE: set .env, run ngrok, POST an order to a verified number"
)
def test_live_outbound_call() -> None:
    """Real end-to-end call once Twilio credentials and a public webhook exist."""
