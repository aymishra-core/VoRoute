import base64
import json
import xml.etree.ElementTree as ET
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from voroute.config import settings
from voroute.main import app
from voroute.models import Order
from voroute.voflow import ConfirmationJob, JobStatus, enqueue
from voroute.store import outcome_count
from voroute.voflow.dispatcher import get_job, note_speech
from voroute.voline import listen, speech
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


def _listen_tail(root: ET.Element, job_id: str) -> None:
    connect = root.find("Connect")
    assert connect is not None
    stream = connect.find("Stream")
    assert stream is not None
    assert stream.attrib["url"] == f"wss://calls.test/voice/stream/{job_id}"
    assert stream.attrib["track"] == "inbound_track"
    assert (
        stream.attrib["statusCallback"]
        == f"https://calls.test/voice/stream-status/{job_id}"
    )
    redirect = root.find("Redirect")
    assert redirect is not None
    assert redirect.attrib["method"] == "POST"
    assert redirect.text == f"https://calls.test/voice/listen-result/{job_id}"
    assert root.find("Hangup") is None
    assert root.find("Gather") is None


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_twiml_endpoint_speaks_the_hinglish_script(
    method: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "elevenlabs_api_key", "")
    monkeypatch.setattr(settings, "deepgram_api_key", "dg-test")
    monkeypatch.setattr(settings, "public_base_url", "https://calls.test")
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
    assert root.find("Play") is None
    _listen_tail(root, job.job_id)


def test_twiml_plays_elevenlabs_audio(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(settings, "elevenlabs_api_key", "test-key")
    monkeypatch.setattr(settings, "elevenlabs_voice_id", "EQUOIWLnCLlSTuJ0h49o")
    monkeypatch.setattr(settings, "deepgram_api_key", "dg-test")
    monkeypatch.setattr(settings, "public_base_url", "https://calls.test")
    monkeypatch.setattr(speech, "AUDIO_DIR", tmp_path)

    def fake_post(url: str, **kwargs: object) -> MagicMock:
        assert url.endswith("/EQUOIWLnCLlSTuJ0h49o")
        headers = kwargs["headers"]
        body = kwargs["json"]
        assert headers["xi-api-key"] == "test-key"
        assert body["model_id"] == "eleven_flash_v2_5"
        assert "Namaste Asha ji" in body["text"]
        response = MagicMock()
        response.status_code = 200
        response.content = b"ID3fake-mp3"
        return response

    monkeypatch.setattr(speech.httpx, "post", fake_post)
    job = enqueue(_order())

    response = client.post(f"/voice/twiml/{job.job_id}")

    assert response.status_code == 200
    root = ET.fromstring(response.text)
    play = root.find("Play")
    assert play is not None
    assert play.text == f"https://calls.test/audio/{job.job_id}.mp3"
    assert root.find("Say") is None
    _listen_tail(root, job.job_id)
    audio = client.get(f"/audio/{job.job_id}.mp3")
    assert audio.status_code == 200
    assert audio.headers["content-type"].startswith("audio/mpeg")
    assert audio.content == b"ID3fake-mp3"


def test_twiml_falls_back_to_say_when_elevenlabs_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "elevenlabs_api_key", "test-key")
    monkeypatch.setattr(settings, "deepgram_api_key", "dg-test")
    monkeypatch.setattr(settings, "public_base_url", "https://calls.test")

    def fake_post(*args: object, **kwargs: object) -> MagicMock:
        response = MagicMock()
        response.status_code = 401
        response.content = b""
        return response

    monkeypatch.setattr(speech.httpx, "post", fake_post)
    job = enqueue(_order())

    response = client.get(f"/voice/twiml/{job.job_id}")

    assert response.status_code == 200
    root = ET.fromstring(response.text)
    say = root.find("Say")
    assert say is not None
    assert say.attrib["voice"] == "Polly.Aditi"
    assert say.attrib["language"] == "hi-IN"
    assert say.text == job.script
    assert root.find("Play") is None
    _listen_tail(root, job.job_id)


def test_missing_deepgram_key_skips_stream_and_offers_keypad(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "elevenlabs_api_key", "")
    monkeypatch.setattr(settings, "deepgram_api_key", "")
    monkeypatch.setattr(settings, "public_base_url", "https://calls.test")
    job = enqueue(_order())

    response = client.post(f"/voice/twiml/{job.job_id}")

    assert response.status_code == 200
    root = ET.fromstring(response.text)
    assert root.find("Say") is not None
    assert root.find("Connect") is None
    assert root.find("Hangup") is None
    gather = root.find("Gather")
    assert gather is not None
    assert gather.attrib["input"] == "dtmf"
    assert gather.attrib["numDigits"] == "1"
    assert gather.attrib["action"] == f"https://calls.test/voice/dtmf/{job.job_id}"
    prompt = gather.find("Say")
    assert prompt is not None
    assert prompt.text == "confirm ke liye 1 dabayein, cancel ke liye 2"
    assert prompt.attrib["voice"] == "Polly.Aditi"


def test_listen_result_records_speech_and_hangs_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "elevenlabs_api_key", "")
    monkeypatch.setattr(settings, "public_base_url", "https://calls.test")
    job = enqueue(_order())
    note_speech(job.job_id, "CONFIRMED", "haan ji")

    response = client.post(f"/voice/listen-result/{job.job_id}")

    assert response.status_code == 200
    root = ET.fromstring(response.text)
    say = root.find("Say")
    assert say is not None
    assert say.text == "Dhanyawaad Asha ji, aapka order confirm kar diya gaya hai."
    assert say.attrib["voice"] == "Polly.Aditi"
    assert root.find("Hangup") is not None
    assert root.find("Gather") is None
    stored = get_job(job.job_id)
    assert stored is not None
    assert stored.status is JobStatus.CONFIRMED
    assert stored.capture_path == "speech"
    again = client.post(f"/voice/listen-result/{job.job_id}")
    assert ET.fromstring(again.text).find("Hangup") is not None
    assert get_job(job.job_id).status is JobStatus.CONFIRMED
    assert outcome_count(job.job_id) == 1


def test_confirmed_close_plays_a_separate_elevenlabs_clip(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(settings, "elevenlabs_api_key", "test-key")
    monkeypatch.setattr(settings, "elevenlabs_voice_id", "EQUOIWLnCLlSTuJ0h49o")
    monkeypatch.setattr(settings, "public_base_url", "https://calls.test")
    monkeypatch.setattr(speech, "AUDIO_DIR", tmp_path)

    def fake_post(url: str, **kwargs: object) -> MagicMock:
        assert url.endswith("/EQUOIWLnCLlSTuJ0h49o")
        body = kwargs["json"]
        assert body["model_id"] == "eleven_flash_v2_5"
        assert body["text"] == (
            "Dhanyawaad Asha ji, aapka order confirm kar diya gaya hai."
        )
        response = MagicMock()
        response.status_code = 200
        response.content = b"ID3close"
        return response

    monkeypatch.setattr(speech.httpx, "post", fake_post)
    job = enqueue(_order())
    note_speech(job.job_id, "CONFIRMED", "haan")

    response = client.post(f"/voice/listen-result/{job.job_id}")

    root = ET.fromstring(response.text)
    play = root.find("Play")
    assert play is not None
    assert play.text == f"https://calls.test/audio/{job.job_id}/close.mp3"
    assert root.find("Say") is None
    assert root.find("Hangup") is not None
    audio = client.get(f"/audio/{job.job_id}/close.mp3")
    assert audio.status_code == 200
    assert audio.content == b"ID3close"
    assert client.get(f"/audio/{job.job_id}.mp3").status_code == 404


def test_listen_result_offers_keypad_when_speech_is_unclear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "public_base_url", "https://calls.test")
    job = enqueue(_order())
    note_speech(job.job_id, "UNCLEAR", "theek hai")

    response = client.post(f"/voice/listen-result/{job.job_id}")

    assert response.status_code == 200
    root = ET.fromstring(response.text)
    gather = root.find("Gather")
    assert gather is not None
    assert gather.find("Say").text == "confirm ke liye 1 dabayein, cancel ke liye 2"
    assert root.find("Hangup") is None
    stored = get_job(job.job_id)
    assert stored is not None
    assert stored.status is JobStatus.CALLING
    assert stored.capture_path == ""


@pytest.mark.parametrize(
    ("digits", "status", "path", "line"),
    [
        (
            "1",
            JobStatus.CONFIRMED,
            "keypad",
            "Dhanyawaad Asha ji, aapka order confirm kar diya gaya hai.",
        ),
        (
            "2",
            JobStatus.DECLINED,
            "keypad",
            "Theek hai Asha ji, aapka order cancel kar diya gaya hai. Dhanyawaad.",
        ),
        (
            "9",
            JobStatus.UNCLEAR,
            "unclear",
            "Dhanyawaad, hum aapse dobara sampark karenge.",
        ),
        (
            "",
            JobStatus.UNCLEAR,
            "unclear",
            "Dhanyawaad, hum aapse dobara sampark karenge.",
        ),
    ],
)
def test_dtmf_records_the_keypad_answer(
    monkeypatch: pytest.MonkeyPatch,
    digits: str,
    status: JobStatus,
    path: str,
    line: str,
) -> None:
    monkeypatch.setattr(settings, "elevenlabs_api_key", "")
    monkeypatch.setattr(settings, "public_base_url", "https://calls.test")
    job = enqueue(_order())
    note_speech(job.job_id, "UNCLEAR", "")

    response = client.post(f"/voice/dtmf/{job.job_id}", data={"Digits": digits})

    assert response.status_code == 200
    root = ET.fromstring(response.text)
    say = root.find("Say")
    assert say is not None
    assert say.text == line
    assert root.find("Hangup") is not None
    if status is JobStatus.UNCLEAR:
        assert "confirm" not in line
        assert "cancel" not in line
    stored = get_job(job.job_id)
    assert stored is not None
    assert stored.status is status
    assert stored.capture_path == path
    assert stored.attempt_count == 0


def test_stream_error_sends_keypad_when_redirect_has_no_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "public_base_url", "https://calls.test")
    sent: list[tuple[str, str]] = []

    def fake_update(call_sid: str, twiml: str) -> None:
        sent.append((call_sid, twiml))

    monkeypatch.setattr("voroute.voline.twiml.update_call_twiml", fake_update)
    job = enqueue(_order())

    response = client.post(
        f"/voice/stream-status/{job.job_id}",
        data={"StreamEvent": "stream-error", "CallSid": "CA_ERR"},
    )

    assert response.status_code == 204
    assert job.status is JobStatus.CALLING
    assert sent[0][0] == "CA_ERR"
    gather = ET.fromstring(sent[0][1]).find("Gather")
    assert gather is not None
    assert gather.find("Say").text == "confirm ke liye 1 dabayein, cancel ke liye 2"


def test_stream_error_hangs_up_a_decisive_speech_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "elevenlabs_api_key", "")
    monkeypatch.setattr(settings, "public_base_url", "https://calls.test")
    sent: list[str] = []

    def fake_update(call_sid: str, twiml: str) -> None:
        sent.append(twiml)

    monkeypatch.setattr("voroute.voline.twiml.update_call_twiml", fake_update)
    job = enqueue(_order())
    note_speech(job.job_id, "DECLINED", "nahi")

    response = client.post(
        f"/voice/stream-status/{job.job_id}",
        data={"StreamEvent": "stream-error", "CallSid": "CA_ERR"},
    )

    assert response.status_code == 204
    stored = get_job(job.job_id)
    assert stored is not None
    assert stored.status is JobStatus.DECLINED
    assert stored.capture_path == "speech"
    root = ET.fromstring(sent[0])
    say = root.find("Say")
    assert say is not None
    assert say.text == (
        "Theek hai Asha ji, aapka order cancel kar diya gaya hai. Dhanyawaad."
    )
    assert root.find("Hangup") is not None
    assert root.find("Gather") is None


def test_stream_stopped_does_not_update_the_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[str] = []
    monkeypatch.setattr(
        "voroute.voline.twiml.update_call_twiml",
        lambda *_args: called.append("called"),
    )
    job = enqueue(_order())

    response = client.post(
        f"/voice/stream-status/{job.job_id}",
        data={"StreamEvent": "stream-stopped", "CallSid": "CA_OK"},
    )

    assert response.status_code == 204
    assert called == []
    assert job.status is JobStatus.CALLING


def test_media_stream_plays_through_to_a_confirmed_hangup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "deepgram_api_key", "dg-test")
    monkeypatch.setattr(settings, "elevenlabs_api_key", "")
    monkeypatch.setattr(settings, "public_base_url", "https://calls.test")
    monkeypatch.setattr(listen, "LISTEN_CAP_S", 2.0)
    seen: dict[str, object] = {}

    class FakeDeepgram:
        def __init__(self) -> None:
            self.sent: list[object] = []

        async def send(self, data: object) -> None:
            self.sent.append(data)
            if isinstance(data, (bytes, bytearray)):
                await self._messages.put(
                    json.dumps(
                        {
                            "type": "Results",
                            "is_final": True,
                            "speech_final": True,
                            "channel": {
                                "alternatives": [
                                    {"transcript": "haan ji", "confidence": 0.91}
                                ]
                            },
                        }
                    )
                )

        def __aiter__(self) -> "FakeDeepgram":
            return self

        async def __anext__(self) -> str:
            import asyncio

            item = await self._messages.get()
            if item is None:
                raise StopAsyncIteration
            return item

        async def __aenter__(self) -> "FakeDeepgram":
            import asyncio

            self._messages = asyncio.Queue()
            return self

        async def __aexit__(self, *_args: object) -> bool:
            await self._messages.put(None)
            return False

    def fake_open() -> FakeDeepgram:
        socket = FakeDeepgram()
        seen["socket"] = socket
        return socket

    monkeypatch.setattr(listen, "open_deepgram", fake_open)
    job = enqueue(_order())
    payload = base64.b64encode(b"\xff").decode()

    with client.websocket_connect(f"/voice/stream/{job.job_id}") as ws:
        ws.send_json({"event": "connected"})
        ws.send_json({"event": "start"})
        ws.send_json({"event": "media", "media": {"payload": payload}})
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()

    socket = seen["socket"]
    assert isinstance(socket, FakeDeepgram)
    assert b"\xff" in socket.sent
    assert json.dumps({"type": "CloseStream"}) in socket.sent
    stored = get_job(job.job_id)
    assert stored is not None
    assert stored.speech_result == "CONFIRMED"
    assert stored.transcript == "haan ji"
    response = client.post(f"/voice/listen-result/{job.job_id}")
    assert ET.fromstring(response.text).find("Hangup") is not None
    finished = get_job(job.job_id)
    assert finished is not None
    assert finished.status is JobStatus.CONFIRMED
    assert finished.capture_path == "speech"


def test_deepgram_url_is_nova3_mulaw_hinglish(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "deepgram_api_key", "dg-test")
    captured: dict[str, object] = {}

    def fake_connect(url: str, **kwargs: object) -> object:
        captured["url"] = url
        captured["headers"] = kwargs["additional_headers"]
        return object()

    monkeypatch.setattr(listen.websockets, "connect", fake_connect)
    listen.open_deepgram()

    url = str(captured["url"])
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert "model=nova-3" in url
    assert "language=multi" in url
    assert "encoding=mulaw" in url
    assert "sample_rate=8000" in url
    assert headers["Authorization"] == "Token dg-test"


# SUBSTAGE-2-LIVE: set .env, run ngrok, POST an order to a verified number.
@pytest.mark.skip(
    reason="SUBSTAGE-2-LIVE: set .env, run ngrok, POST an order to a verified number"
)
def test_live_outbound_call() -> None:
    """Real end-to-end call once Twilio credentials and a public webhook exist."""
