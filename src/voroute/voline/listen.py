"""One-turn listen: Twilio Media Stream audio forwarded to Deepgram, then the socket closes."""

import asyncio
import base64
import json
import logging
import time
from typing import Any

import websockets
from fastapi import APIRouter, WebSocket

from voroute.config import settings
from voroute.voflow.dispatcher import get_job, note_speech
from voroute.vowrap.classify import classify

logger = logging.getLogger("voroute.voline")

router = APIRouter()

LISTEN_CAP_S = 10.0
DEEPGRAM_URL = (
    "wss://api.deepgram.com/v1/listen"
    "?model=nova-3"
    "&language=multi"
    "&encoding=mulaw"
    "&sample_rate=8000"
    "&channels=1"
    "&interim_results=true"
    "&utterance_end_ms=1000"
    "&endpointing=300"
    "&vad_events=true"
)


def open_deepgram() -> Any:
    return websockets.connect(
        DEEPGRAM_URL,
        additional_headers={
            "Authorization": f"Token {settings.deepgram_api_key.strip()}"
        },
        open_timeout=10,
    )


def parse_deepgram(
    raw: Any,
) -> tuple[str, str, bool, bool, float | None] | None:
    """Results and UtteranceEnd only. Unknown shapes are ignored."""

    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    kind = str(data.get("type") or "")
    if kind == "UtteranceEnd":
        return "utterance_end", "", False, False, None
    if kind != "Results":
        return None
    channel = data.get("channel")
    if isinstance(channel, list):
        channel = channel[0] if channel else None
    if not isinstance(channel, dict):
        return None
    alternatives = channel.get("alternatives")
    if not isinstance(alternatives, list) or not alternatives:
        return None
    alt = alternatives[0]
    if not isinstance(alt, dict):
        return None
    transcript = str(alt.get("transcript") or "").strip()
    confidence = alt.get("confidence")
    score = float(confidence) if isinstance(confidence, (int, float)) else None
    return (
        "transcript",
        transcript,
        bool(data.get("is_final")),
        bool(data.get("speech_final")),
        score,
    )


def absorb_deepgram(
    raw: Any, finals: list[str], scores: list[float]
) -> bool:
    """Append a final piece. Stop after the first completed utterance."""

    parsed = parse_deepgram(raw)
    if parsed is None:
        return False
    kind, transcript, is_final, speech_final, confidence = parsed
    if kind == "transcript" and is_final and transcript:
        finals.append(transcript)
        if confidence is not None:
            scores.append(confidence)
    if speech_final and finals:
        return True
    return kind == "utterance_end" and bool(finals)


@router.websocket("/voice/stream/{job_id}")
async def confirmation_stream(websocket: WebSocket, job_id: str) -> None:
    if get_job(job_id) is None:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    try:
        await listen_turn(websocket, job_id)
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


async def listen_turn(websocket: WebSocket, job_id: str) -> None:
    text = ""
    confidence: float | None = None
    try:
        if not settings.deepgram_api_key.strip():
            raise RuntimeError("DEEPGRAM_API_KEY is not set")
        async with open_deepgram() as deepgram:
            text, confidence = await _one_utterance(websocket, deepgram)
    except Exception as exc:
        logger.info("job_id=%s speech=unclear reason=%s", job_id, exc)
        text = ""
        confidence = None
    verdict = classify(text, confidence)
    note_speech(job_id, verdict.value, text)
    logger.info(
        "job_id=%s speech=%s confidence=%s transcript=%s",
        job_id,
        verdict.value,
        "n/a" if confidence is None else f"{confidence:.2f}",
        text,
    )


async def _one_utterance(websocket: WebSocket, deepgram: Any) -> tuple[str, float | None]:
    finals: list[str] = []
    scores: list[float] = []
    events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    stop = asyncio.Event()

    async def pump_twilio() -> None:
        try:
            while not stop.is_set():
                raw = await websocket.receive_text()
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(message, dict):
                    await events.put(message)
        except Exception:
            await events.put({"event": "stop"})

    async def pump_deepgram() -> None:
        try:
            async for raw in deepgram:
                await events.put({"event": "deepgram", "raw": raw})
        except Exception:
            await events.put({"event": "stop"})

    twilio_task = asyncio.create_task(pump_twilio())
    deepgram_task = asyncio.create_task(pump_deepgram())
    try:
        deadline = time.monotonic() + LISTEN_CAP_S
        while time.monotonic() < deadline:
            try:
                event = await asyncio.wait_for(
                    events.get(), timeout=deadline - time.monotonic()
                )
            except asyncio.TimeoutError:
                break
            kind = event.get("event")
            if kind == "media":
                media = event.get("media")
                payload = ""
                if isinstance(media, dict):
                    payload = str(media.get("payload") or "")
                if payload:
                    await deepgram.send(base64.b64decode(payload))
            elif kind == "stop":
                break
            elif kind == "deepgram" and absorb_deepgram(event.get("raw"), finals, scores):
                break
        try:
            await deepgram.send(json.dumps({"type": "CloseStream"}))
        except Exception:
            pass
    finally:
        stop.set()
        twilio_task.cancel()
        deepgram_task.cancel()
        await asyncio.gather(twilio_task, deepgram_task, return_exceptions=True)
    text = " ".join(part for part in finals if part).strip()
    confidence = sum(scores) / len(scores) if scores else None
    return text, confidence
