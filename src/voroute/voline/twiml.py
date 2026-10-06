"""Twilio voice webhook. Plays the question, then listens; keypad only if speech is not decisive."""

import logging
import re
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response

from voroute.config import settings
from voroute.voflow.dispatcher import (
    AfterListen,
    apply_digits,
    apply_listen_result,
    apply_stream_error,
    get_job,
)
from voroute.voflow.script import build_closing
from voroute.voline.provider import update_call_twiml
from voroute.voline.speech import SpeechError, audio_path, synthesize

logger = logging.getLogger("voroute.voline")

router = APIRouter()

SAY_VOICE = "Polly.Aditi"
SAY_LANGUAGE = "hi-IN"
KEYPAD_PROMPT = "confirm ke liye 1 dabayein, cancel ke liye 2"

_JOB_ID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _https_base() -> str:
    return settings.public_base_url.rstrip("/")


def _wss_base() -> str:
    base = _https_base()
    if base.startswith("https://"):
        return "wss://" + base.removeprefix("https://")
    if base.startswith("http://"):
        return "ws://" + base.removeprefix("http://")
    return base


def _voice_response() -> object:
    from twilio.twiml.voice_response import VoiceResponse

    return VoiceResponse()


def render_hangup() -> str:
    response = _voice_response()
    response.hangup()
    return str(response)


def render_close(job_id: str) -> str:
    """Play the outcome line, then hang up. Polly speaks it if ElevenLabs does not."""

    job = get_job(job_id)
    if job is None or job.status.value not in {"CONFIRMED", "DECLINED", "UNCLEAR"}:
        return render_hangup()
    line = build_closing(job.customer_name, job.status.value)
    response = _voice_response()
    try:
        synthesize(job.job_id, line, clip="close")
    except SpeechError as exc:
        logger.info("job_id=%s voice=say-fallback reason=%s", job.job_id, exc)
        response.say(line, voice=SAY_VOICE, language=SAY_LANGUAGE)
    else:
        play_url = f"{_https_base()}/audio/{job.job_id}/close.mp3"
        logger.info("job_id=%s voice=elevenlabs close=%s", job.job_id, play_url)
        response.play(play_url)
    response.hangup()
    return str(response)


def _append_gather(response: object, job_id: str) -> None:
    gather = response.gather(
        input="dtmf",
        num_digits=1,
        timeout=6,
        action=f"{_https_base()}/voice/dtmf/{job_id}",
        method="POST",
    )
    gather.say(KEYPAD_PROMPT, voice=SAY_VOICE, language=SAY_LANGUAGE)


def render_gather(job_id: str) -> str:
    response = _voice_response()
    _append_gather(response, job_id)
    return str(response)


def _append_listen(response: object, job_id: str) -> None:
    if not settings.deepgram_api_key.strip():
        _append_gather(response, job_id)
        return
    connect = response.connect()
    connect.stream(
        url=f"{_wss_base()}/voice/stream/{job_id}",
        track="inbound_track",
        status_callback=f"{_https_base()}/voice/stream-status/{job_id}",
        status_callback_method="POST",
    )
    response.redirect(f"{_https_base()}/voice/listen-result/{job_id}", method="POST")


def render_say(script: str, job_id: str) -> str:
    response = _voice_response()
    response.say(script, voice=SAY_VOICE, language=SAY_LANGUAGE)
    _append_listen(response, job_id)
    return str(response)


def render_play(url: str, job_id: str) -> str:
    response = _voice_response()
    response.play(url)
    _append_listen(response, job_id)
    return str(response)


def _xml(action: AfterListen, job_id: str) -> Response:
    if action is AfterListen.GATHER:
        body = render_gather(job_id)
    else:
        body = render_close(job_id)
    return Response(content=body, media_type="application/xml")


@router.api_route("/voice/twiml/{job_id}", methods=["GET", "POST"])
def confirmation_twiml(job_id: str) -> Response:
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    try:
        synthesize(job.job_id, job.script)
    except SpeechError as exc:
        logger.info("job_id=%s voice=say-fallback reason=%s", job.job_id, exc)
        xml = render_say(job.script, job.job_id)
    else:
        play_url = f"{_https_base()}/audio/{job.job_id}.mp3"
        logger.info("job_id=%s voice=elevenlabs play=%s", job.job_id, play_url)
        xml = render_play(play_url, job.job_id)
    return Response(content=xml, media_type="application/xml")


@router.api_route("/voice/listen-result/{job_id}", methods=["GET", "POST"])
def listen_result(job_id: str) -> Response:
    action = apply_listen_result(job_id)
    if action is AfterListen.NONE:
        raise HTTPException(status_code=404, detail="job not found")
    return _xml(action, job_id)


@router.api_route("/voice/dtmf/{job_id}", methods=["GET", "POST"])
async def dtmf_result(job_id: str, request: Request) -> Response:
    form = await _form(request)
    action = apply_digits(job_id, form.get("Digits", ""))
    if action is AfterListen.NONE:
        raise HTTPException(status_code=404, detail="job not found")
    return _xml(action, job_id)


@router.post("/voice/stream-status/{job_id}")
async def stream_status(job_id: str, request: Request) -> Response:
    form = await _form(request)
    if form.get("StreamEvent") != "stream-error":
        return Response(status_code=204)
    action = apply_stream_error(job_id)
    call_sid = form.get("CallSid", "")
    if action is AfterListen.NONE or not call_sid:
        return Response(status_code=204)
    twiml = render_close(job_id) if action is AfterListen.HANGUP else render_gather(job_id)
    try:
        update_call_twiml(call_sid, twiml)
    except Exception as exc:
        logger.info("job_id=%s voice=stream-error update-failed reason=%s", job_id, exc)
    else:
        logger.info(
            "job_id=%s voice=stream-error action=%s",
            job_id,
            "hangup" if action is AfterListen.HANGUP else "keypad",
        )
    return Response(status_code=204)


@router.get("/audio/{job_id}.mp3")
def confirmation_audio(job_id: str) -> FileResponse:
    if _JOB_ID.fullmatch(job_id) is None:
        raise HTTPException(status_code=404, detail="audio not found")
    path = audio_path(job_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="audio not found")
    return FileResponse(path, media_type="audio/mpeg")


@router.get("/audio/{job_id}/close.mp3")
def closing_audio(job_id: str) -> FileResponse:
    if _JOB_ID.fullmatch(job_id) is None:
        raise HTTPException(status_code=404, detail="audio not found")
    path = audio_path(job_id, "close")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="audio not found")
    return FileResponse(path, media_type="audio/mpeg")


async def _form(request: Request) -> dict[str, str]:
    parsed = parse_qs((await request.body()).decode(), keep_blank_values=True)
    return {key: values[-1] for key, values in parsed.items()}
