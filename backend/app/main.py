from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import ConfigurationError, DeepgramSettings
from app.services.browser_session import (
    BrowserDeepgramSession,
    BrowserLiveWpmSession,
)
from app.services.deepgram_transcription import (
    DeepgramAudioMode,
    DeepgramTranscriptionSession,
)


STATIC_DIRECTORY = Path(__file__).parent / "static"
ProviderFactory = Callable[[], BrowserDeepgramSession]


def _browser_provider() -> BrowserDeepgramSession:
    return DeepgramTranscriptionSession(
        DeepgramSettings.from_environment(),
        audio_mode=DeepgramAudioMode.WEBM_OPUS,
    )


def create_app(*, provider_factory: ProviderFactory | None = None) -> FastAPI:
    application = FastAPI(title="Speech Speedometer")
    make_provider = _browser_provider if provider_factory is None else provider_factory
    application.mount("/static", StaticFiles(directory=STATIC_DIRECTORY), name="static")

    @application.get("/", response_class=FileResponse)
    async def debug_client() -> FileResponse:
        return FileResponse(STATIC_DIRECTORY / "index.html")

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.websocket("/ws/live")
    async def live_wpm(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            provider = make_provider()
        except ConfigurationError:
            await websocket.send_json(
                {"type": "error", "message": "Live transcription is not configured"}
            )
        else:
            await BrowserLiveWpmSession(websocket, provider).run()
        try:
            await websocket.close()
        except (RuntimeError, WebSocketDisconnect):
            pass

    return application


app = create_app()
