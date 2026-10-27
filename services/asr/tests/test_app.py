"""Эндпоинт /v1/audio/transcriptions (спека #17, разделы 5–7). Модель заменена заглушками."""

from pathlib import Path

import anyio
import httpx
import numpy as np
import pytest
import structlog
from fastapi.testclient import TestClient

from clin_compass_asr.app import create_app
from clin_compass_asr.recognizer import Transcriber
from clin_compass_asr.segments import Segmenter
from tests.fakes import VOCAB, FakeRecognizer
from tests.media import encode, encode_wav, sine

URL = "/v1/audio/transcriptions"
TEXT = "Доктор, я принимаю Авиандр 50 мг утром."


def _app(greedy: FakeRecognizer, beam: FakeRecognizer) -> TestClient:
    # Записи в тестах короче 20 с: Segmenter отдаёт их целиком, модель VAD не читается.
    transcriber = Transcriber(
        greedy=greedy,
        beam=beam,
        split=Segmenter(Path("/nonexistent/silero_vad.onnx")).split,
        allowed_chars=VOCAB,
    )
    return TestClient(create_app(transcriber=transcriber))


def _webm(seconds: float = 1.0) -> bytes:
    return encode(sine(seconds, 48_000), rate=48_000, container="webm", codec="libopus")


def test_transcribes_with_hotwords() -> None:
    beam = FakeRecognizer([TEXT])
    client = _app(FakeRecognizer(["greedy"]), beam)

    response = client.post(
        URL,
        files={"file": ("note.webm", _webm(), "audio/webm")},
        data={"hotwords": ["Авиандр", "Сертралин"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["text"] == TEXT
    assert body["duration"] == pytest.approx(1.0, abs=0.03)
    assert [s.hotwords for s in beam.streams] == ["Авиандр/Сертралин"]


def test_transcribes_without_hotwords_with_greedy() -> None:
    greedy = FakeRecognizer([TEXT])
    client = _app(greedy, FakeRecognizer(["beam"]))

    response = client.post(URL, files={"file": ("note.m4a", _mp4(), "audio/mp4")})

    assert response.status_code == 200
    assert response.json()["text"] == TEXT
    assert greedy.decode_calls == 1


def _mp4() -> bytes:
    return encode(sine(1.0, 22_050), rate=22_050, container="mp4", codec="aac")


def test_rejects_file_over_25_mb_before_decoding() -> None:
    greedy = FakeRecognizer([TEXT])
    client = _app(greedy, FakeRecognizer([]))

    response = client.post(URL, files={"file": ("big.webm", b"\0" * (25 * 1024 * 1024 + 1))})

    assert response.status_code == 413
    assert greedy.decode_calls == 0


def test_rejects_recording_over_10_minutes() -> None:
    # Тишина кодируется в 6 раз быстрее синуса; для проверки лимита содержимое не важно.
    silence = np.zeros(601 * 8_000, dtype=np.float32)
    long_recording = encode(silence, rate=8_000, container="webm", codec="libopus")
    client = _app(FakeRecognizer([TEXT]), FakeRecognizer([]))

    response = client.post(URL, files={"file": ("long.webm", long_recording)})

    assert response.status_code == 413


def test_rejects_unsupported_format_with_415() -> None:
    client = _app(FakeRecognizer([TEXT]), FakeRecognizer([]))

    response = client.post(
        URL, files={"file": ("note.wav", encode_wav(sine(1.0, 16_000), rate=16_000))}
    )

    assert response.status_code == 415
    assert "webm/opus или mp4/aac" in response.json()["detail"]


def test_rejects_unreadable_file_with_422() -> None:
    client = _app(FakeRecognizer([TEXT]), FakeRecognizer([]))

    response = client.post(URL, files={"file": ("note.webm", b"not audio" * 100)})

    assert response.status_code == 422


def test_rejects_bad_hotword_with_422_without_echoing_it() -> None:
    beam = FakeRecognizer([TEXT])
    client = _app(FakeRecognizer([]), beam)

    response = client.post(
        URL,
        files={"file": ("note.webm", _webm())},
        data={"hotwords": ["Авиандр", "Авиандр ретард"]},
    )

    assert response.status_code == 422
    assert "ретард" not in response.text
    assert beam.decode_calls == 0


def test_logs_have_no_text_and_no_hotwords() -> None:
    client = _app(FakeRecognizer([]), FakeRecognizer([TEXT]))

    with structlog.testing.capture_logs() as logs:
        response = client.post(
            URL, files={"file": ("note.webm", _webm())}, data={"hotwords": ["Авиандр"]}
        )

    assert response.status_code == 200
    [event] = [e for e in logs if e["event"] == "asr.transcribed"]
    assert event["decoding"] == "modified_beam_search"
    assert event["hotwords"] == 1
    assert event["segments"] == 1
    assert event["duration"] == pytest.approx(1.0, abs=0.03)
    assert event["rtf"] >= 0
    logged = repr(logs)
    assert "Авиандр" not in logged
    assert "принимаю" not in logged


def test_rejection_is_logged_by_reason_only() -> None:
    client = _app(FakeRecognizer([]), FakeRecognizer([]))

    with structlog.testing.capture_logs() as logs:
        client.post(
            URL,
            files={"file": ("note.wav", encode_wav(sine(1.0, 16_000), rate=16_000))},
            data={"hotwords": ["Авиандр"]},
        )

    [event] = [e for e in logs if e["event"] == "asr.rejected"]
    assert event["status"] == 415
    assert "Авиандр" not in repr(logs)


@pytest.mark.anyio
async def test_one_recording_at_a_time() -> None:
    greedy = FakeRecognizer([TEXT], delay=0.2)
    transcriber = Transcriber(
        greedy=greedy,
        beam=FakeRecognizer([]),
        split=Segmenter(Path("/nonexistent/silero_vad.onnx")).split,
        allowed_chars=VOCAB,
    )
    app = create_app(transcriber=transcriber)
    data = _webm()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://asr"
    ) as client:

        async def post() -> None:
            response = await client.post(URL, files={"file": ("note.webm", data)})
            assert response.status_code == 200

        async with anyio.create_task_group() as tg:
            for _ in range(3):
                tg.start_soon(post)

    assert greedy.decode_calls == 3
    assert greedy.max_active == 1


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_validation_error_does_not_echo_request() -> None:
    client = _app(FakeRecognizer([]), FakeRecognizer([]))

    # Вместо файла пришёл текст: встроенная ошибка FastAPI повторила бы его в поле input.
    response = client.post(URL, data={"file": "Принимаю Авиандр", "hotwords": ["Сертралин"]})

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "file"]
    assert "Авиандр" not in response.text
    assert "Сертралин" not in response.text


def test_health_answers_once_model_is_loaded() -> None:
    # uvicorn принимает соединения только после lifespan, то есть после загрузки модели.
    client = _app(FakeRecognizer(["greedy"]), FakeRecognizer(["beam"]))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
