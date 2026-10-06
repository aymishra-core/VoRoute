"""Turn a fixed confirmation script into an MP3 Twilio can fetch."""

from pathlib import Path

import httpx

from voroute.config import settings

AUDIO_DIR = Path("audio")
MODEL_ID = "eleven_flash_v2_5"
DEFAULT_VOICE_ID = "EQUOIWLnCLlSTuJ0h49o"
_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"


class SpeechError(Exception):
    """ElevenLabs did not produce audio. The call can still use <Say>."""


def audio_path(job_id: str, clip: str = "") -> Path:
    stem = f"{job_id}-{clip}" if clip else job_id
    return AUDIO_DIR / f"{stem}.mp3"


def synthesize(job_id: str, text: str, clip: str = "") -> Path:
    key = settings.elevenlabs_api_key.strip()
    if not key:
        raise SpeechError("ELEVENLABS_API_KEY is not set")
    voice_id = settings.elevenlabs_voice_id.strip() or DEFAULT_VOICE_ID
    try:
        response = httpx.post(
            _TTS_URL.format(voice_id=voice_id),
            headers={
                "xi-api-key": key,
                "Accept": "audio/mpeg",
                "Content-Type": "application/json",
            },
            json={"text": text, "model_id": MODEL_ID},
            timeout=20.0,
        )
    except httpx.HTTPError as exc:
        raise SpeechError(str(exc)) from exc
    if response.status_code != 200 or not response.content:
        raise SpeechError(f"elevenlabs status={response.status_code}")
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    path = audio_path(job_id, clip)
    path.write_bytes(response.content)
    return path
