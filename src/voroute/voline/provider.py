"""Outbound-call providers. An ExotelProvider can implement the same place_call seam later."""

from typing import Any, Protocol

from voroute.config import settings
from voroute.voflow.job import ConfirmationJob


class TelephonyProvider(Protocol):
    def place_call(self, to: str, job: ConfirmationJob) -> str:
        """Place an outbound call and return the provider call id."""


class TwilioProvider:
    """Twilio REST client. Constructing this does not import or call Twilio."""

    def __init__(self, client: Any | None = None) -> None:
        self._client = client

    def _rest_client(self) -> Any:
        if self._client is None:
            from twilio.rest import Client

            self._client = Client(
                settings.twilio_account_sid,
                settings.twilio_auth_token,
            )
        return self._client

    def place_call(self, to: str, job: ConfirmationJob) -> str:
        base = settings.public_base_url.rstrip("/")
        # Twilio's only terminal event name is "completed". busy, no-answer,
        # failed, and canceled arrive as CallStatus on that same callback.
        call = self._rest_client().calls.create(
            to=to,
            from_=settings.twilio_from_number,
            url=f"{base}/voice/twiml/{job.job_id}",
            status_callback=f"{base}/voice/call-status/{job.job_id}",
            status_callback_method="POST",
            status_callback_event=["completed"],
        )
        return str(call.sid)


_provider: TelephonyProvider | None = None


def get_provider() -> TelephonyProvider:
    global _provider
    if _provider is None:
        _provider = TwilioProvider()
    return _provider


def set_provider(provider: TelephonyProvider | None) -> None:
    global _provider
    _provider = provider


def place_call(to: str, job: ConfirmationJob) -> str:
    return get_provider().place_call(to, job)


def update_call_twiml(call_sid: str, twiml: str) -> None:
    """Replace the live call's instructions. Used when a media stream errors."""

    from twilio.rest import Client

    Client(settings.twilio_account_sid, settings.twilio_auth_token).calls(
        call_sid
    ).update(twiml=twiml)
