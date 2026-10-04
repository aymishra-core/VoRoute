"""Twilio voice webhook. Speaks the job script and hangs up; media streaming comes later."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from voroute.voflow.dispatcher import get_job

router = APIRouter()


def render_twiml(script: str) -> str:
    from twilio.twiml.voice_response import VoiceResponse

    response = VoiceResponse()
    response.say(script, voice="Polly.Aditi", language="hi-IN")
    response.hangup()
    return str(response)


@router.api_route("/voice/twiml/{job_id}", methods=["GET", "POST"])
def confirmation_twiml(job_id: str) -> Response:
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return Response(content=render_twiml(job.script), media_type="application/xml")
