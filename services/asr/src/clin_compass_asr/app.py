"""HTTP-интерфейс сервиса ASR (спека #17, разделы 5–7).

Запуск: `services/asr/serve.sh` (uvicorn --factory clin_compass_asr.app:create_app).
Текст записи и подсказки — медицинские данные: в лог они не попадают никогда.
"""

import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Annotated

import anyio
import anyio.to_thread
import structlog
from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from clin_compass_asr import audio
from clin_compass_asr.recognizer import InvalidHotwordError, Transcriber, Transcript, check_hotwords
from clin_compass_asr.settings import Settings

MAX_UPLOAD_BYTES = 25 * 1024 * 1024

log = structlog.get_logger()


class TranscriptionResponse(BaseModel):
    """Текст записи и её длительность в секундах."""

    text: str
    duration: float


def create_app(transcriber: Transcriber | None = None) -> FastAPI:
    """Собирает приложение. Без `transcriber` модель грузится при старте из `Settings`.

    Args:
        transcriber: Готовый распознаватель; тесты передают его с заглушками вместо модели.
    """
    _configure_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if transcriber is None:
            settings = Settings()
            # Нет файлов модели — ModelFilesMissingError, uvicorn завершается с её текстом.
            app.state.transcriber = Transcriber.load(
                settings.model_dir, num_threads=settings.num_threads
            )
        yield

    app = FastAPI(title="Клинический компас: ASR", lifespan=lifespan)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.state.transcriber = transcriber
    # Распознавание грузит процессор: одна запись за раз, параллельность — внутри ONNX Runtime.
    app.state.limiter = anyio.CapacityLimiter(1)

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Готовность для make inference-up и health бэкенда (спека #18, раздел 3).

        uvicorn принимает соединения только после lifespan, поэтому ответ значит «модель загружена».
        """
        return {"status": "ok"}

    @app.post("/v1/audio/transcriptions")
    async def transcriptions(
        file: UploadFile,
        hotwords: Annotated[list[str] | None, Form()] = None,
    ) -> TranscriptionResponse:
        """Распознаёт запись webm/opus или mp4/aac. `hotwords` — повторяемое поле."""
        current: Transcriber = app.state.transcriber
        # Starlette уже принял файл целиком; в память читается не больше лимита + 1 байт.
        data = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise _rejected(413, f"файл больше {MAX_UPLOAD_BYTES // (1024 * 1024)} МБ")
        try:
            words = check_hotwords(hotwords or [], current.allowed_chars)
        except InvalidHotwordError as exc:
            raise _rejected(422, str(exc)) from exc

        try:
            duration, transcript, elapsed = await anyio.to_thread.run_sync(
                _transcribe, current, data, words, limiter=app.state.limiter
            )
        except audio.UnsupportedAudioError as exc:
            raise _rejected(415, str(exc)) from exc
        except audio.AudioTooLongError as exc:
            raise _rejected(413, str(exc)) from exc
        except audio.UnreadableAudioError as exc:
            raise _rejected(422, str(exc)) from exc

        log.info(
            "asr.transcribed",
            duration=round(duration, 2),
            segments=transcript.segments,
            decoding=transcript.decoding,
            hotwords=len(words),
            elapsed=round(elapsed, 3),
            rtf=round(elapsed / duration, 3) if duration else None,
        )
        return TranscriptionResponse(text=transcript.text, duration=round(duration, 2))

    return app


def _transcribe(
    transcriber: Transcriber, data: bytes, hotwords: Sequence[str]
) -> tuple[float, Transcript, float]:
    """Декодирование и распознавание в рабочем потоке: длительность, текст, время обработки."""
    started = time.perf_counter()
    samples = audio.decode(data)
    transcript = transcriber.transcribe(samples, hotwords)
    return len(samples) / audio.SAMPLE_RATE, transcript, time.perf_counter() - started


def _rejected(status: int, reason: str) -> HTTPException:
    """Ошибка запроса. `reason` не содержит данных записи и подсказок."""
    log.warning("asr.rejected", status=status, reason=reason)
    return HTTPException(status_code=status, detail=reason)


async def _validation_error(_request: Request, exc: Exception) -> JSONResponse:
    """Ошибка 422 без поля input: встроенный ответ FastAPI повторяет присланное значение."""
    errors = exc.errors() if isinstance(exc, RequestValidationError) else []
    detail = [{"loc": e["loc"], "type": e["type"], "msg": e["msg"]} for e in errors]
    log.warning("asr.rejected", status=422, reason=[(e["loc"], e["type"]) for e in detail])
    return JSONResponse(status_code=422, content={"detail": detail})


def _configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ]
    )
