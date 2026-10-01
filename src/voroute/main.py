"""FastAPI entrypoint for the VoRoute server."""

from fastapi import FastAPI

from voroute.config import settings

app = FastAPI(title="VoRoute", version="0.1.0")
app.state.settings = settings


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "voroute"}
