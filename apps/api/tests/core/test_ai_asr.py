"""Клиент ASR: запись → текст через services/asr, узлы по порядку, запись в ai_runs (спека #18)."""

import asyncio
import traceback
from typing import Any

import pytest
import structlog

from clin_compass.core.ai.asr import AsrRejectedError, Transcript, build_asr_client
from clin_compass.core.ai.nodes import (
    AiNoAnswerError,
    AiNotConfiguredError,
    AiOutputError,
    AiServiceError,
    NodesUnavailableError,
)
from clin_compass.core.config import Settings
from tests.core.fake_nodes import MemoryRecorder, Reply, Request, dead_node_url, fake_node

AUDIO = b"\x1a\x45\xdf\xa3 webm"
TEXT = "Доктор, я принимаю Авиандр 50 мг утром."


def _asr(request: Request) -> Reply:
    return 200, {"text": TEXT, "duration": 8.61}


def _settings(*urls: str, **overrides: Any) -> Settings:
    # Значения передаём явно: Makefile экспортирует .env, где может стоять настоящий адрес.
    return Settings(
        database_url="postgresql+asyncpg://unused/none",
        environment="test",
        asr_urls=urls,
        **overrides,
    )


async def _transcribe(settings: Settings, recorder: MemoryRecorder, **kwargs: Any) -> Any:
    client = build_asr_client(settings, recorder)
    return await client.transcribe(
        AUDIO, filename="note.webm", content_type="audio/webm", task="diary.transcribe", **kwargs
    )


async def test_transcript_is_returned_and_recorded_without_text() -> None:
    recorder = MemoryRecorder()

    async with fake_node(_asr) as node:
        result = await _transcribe(_settings(node.url), recorder, hotwords=["Авиандр", "Сертралин"])

    assert result.output == Transcript(text=TEXT, duration=8.61)
    [run] = recorder.runs
    assert result.run_id is not None
    assert (run.role, run.task, run.status, run.node) == ("asr", "diary.transcribe", "ok", node.url)
    assert (run.model, run.audio_seconds) == ("gigaam-v3-e2e-rnnt", 8.61)
    assert run.params == {"hotwords": 2, "decoding": "beam"}
    assert "Авиандр" not in repr(run)


async def test_audio_and_hotwords_are_sent_as_multipart_fields() -> None:
    async with fake_node(_asr) as node:
        await _transcribe(_settings(node.url), MemoryRecorder(), hotwords=["Авиандр", "Сертралин"])

    [request] = node.requests
    assert (request.method, request.path) == ("POST", "/v1/audio/transcriptions")
    assert request.headers["content-type"].startswith("multipart/form-data")
    assert b'name="file"; filename="note.webm"' in request.body
    assert b"Content-Type: audio/webm" in request.body
    assert AUDIO in request.body
    assert request.body.count(b'name="hotwords"') == 2
    assert "Авиандр".encode() in request.body
    assert "Сертралин".encode() in request.body


async def test_without_hotwords_no_hotwords_field_is_sent() -> None:
    recorder = MemoryRecorder()

    async with fake_node(_asr) as node:
        await _transcribe(_settings(node.url), recorder)

    assert b'name="hotwords"' not in node.requests[0].body
    assert recorder.runs[0].params == {"hotwords": 0, "decoding": "greedy"}


@pytest.mark.parametrize("status", [413, 415, 422])
async def test_rejected_audio_is_not_sent_to_next_node(status: int) -> None:
    recorder = MemoryRecorder()

    async with (
        fake_node(lambda request: (status, {"detail": "формат не поддерживается"})) as strict,
        fake_node(_asr) as spare,
    ):
        with pytest.raises(AsrRejectedError) as caught:
            await _transcribe(_settings(strict.url, spare.url), recorder)

    assert (caught.value.status, caught.value.detail) == (status, "формат не поддерживается")
    # В цепочке причин был бы ответ узла с телом.
    assert caught.value.__cause__ is None
    assert spare.requests == []
    assert recorder.runs[0].error == f"AsrRejectedError {status}"


async def test_server_error_is_not_sent_to_next_node() -> None:
    async with (
        fake_node(lambda request: (500, {"detail": "Internal Server Error"})) as broken,
        fake_node(_asr) as spare,
    ):
        with pytest.raises(AiServiceError) as caught:
            await _transcribe(_settings(broken.url, spare.url), MemoryRecorder())

    assert caught.value.status == 500
    assert caught.value.__cause__ is None
    assert spare.requests == []


@pytest.mark.parametrize("first", ["dead", "not_ready"])
async def test_answer_comes_from_next_node_when_first_is_unavailable(first: str) -> None:
    recorder = MemoryRecorder()

    async with (
        fake_node(lambda request: (503, {"detail": "starting"})) as not_ready,
        fake_node(_asr) as live,
    ):
        first_url = dead_node_url() if first == "dead" else not_ready.url
        with structlog.testing.capture_logs() as logs:
            result = await _transcribe(_settings(first_url, live.url), recorder)

    assert result.output.text == TEXT
    assert recorder.runs[0].node == live.url
    # Переход к следующему узлу виден в логе; текста записи и подсказок там нет.
    assert logs == [
        {
            "event": "ai.node_unavailable",
            "log_level": "warning",
            "role": "asr",
            "node": first_url,
            "error": "ConnectError" if first == "dead" else "HTTPStatusError",
        }
    ]


async def test_all_nodes_down_raises_nodes_unavailable_and_is_recorded() -> None:
    recorder = MemoryRecorder()

    with pytest.raises(NodesUnavailableError):
        await _transcribe(_settings(dead_node_url(), dead_node_url()), recorder)

    [run] = recorder.runs
    assert (run.status, run.error, run.node) == ("error", "NodesUnavailableError", None)


def test_build_asr_client_requires_node_urls() -> None:
    with pytest.raises(AiNotConfiguredError, match="COMPASS_ASR_URLS"):
        build_asr_client(_settings(), MemoryRecorder())


async def test_answer_off_schema_raises_output_error_without_transcript() -> None:
    # Ошибка pydantic повторила бы текст записи в input_value; в ошибку он не попадает.
    recorder = MemoryRecorder()

    async with fake_node(lambda request: (200, {"text": TEXT, "duration": "долго"})) as node:
        with pytest.raises(AiOutputError) as caught:
            await _transcribe(_settings(node.url), recorder)

    error = caught.value
    assert error.role == "asr"
    assert error.__cause__ is None
    assert error.__suppress_context__
    assert TEXT not in "".join(traceback.format_exception(error))
    assert recorder.runs[0].error == "AiOutputError"


async def _hang(request: Request) -> Reply:
    await asyncio.sleep(1)
    return _asr(request)


@pytest.mark.parametrize("handler", [_hang, lambda request: None], ids=["timeout", "dropped"])
async def test_no_answer_is_not_sent_to_next_node(handler: Any) -> None:
    # Запрос дошёл до сервиса: повтор на другом узле удвоил бы нагрузку. Повторяет очередь.
    recorder = MemoryRecorder()

    async with fake_node(handler) as silent, fake_node(_asr) as spare:
        settings = _settings(silent.url, spare.url, asr_timeout_seconds=0.2)
        with pytest.raises(AiNoAnswerError) as caught:
            await _transcribe(settings, recorder)

    assert spare.requests == []
    assert caught.value.__cause__ is None
    assert recorder.runs[0].error == "AiNoAnswerError"
