"""Клиент ASR: голосовая запись → текст через `services/asr` (спеки #17 и #18).

Запрос — `POST <узел>/v1/audio/transcriptions`, multipart: файл и повторяемое поле `hotwords`.
Текст записи и подсказки — медицинские данные: в ai_runs попадают только их число и хеш записи.
"""

import json
from collections.abc import Sequence

import httpx2
from pydantic import BaseModel, ConfigDict, ValidationError

from clin_compass.core.ai.nodes import (
    AiError,
    AiNoAnswerError,
    AiOutputError,
    AiServiceError,
    NodesUnavailableError,
    is_unavailable,
    node_urls,
    report_unavailable,
)
from clin_compass.core.ai.runs import AiResult, RunRecorder, hash_input, track_run
from clin_compass.core.config import Settings

# GigaAM-v3 e2e_rnnt в sherpa-onnx (services/asr/versions.env, MODEL_NAME).
MODEL_NAME = "gigaam-v3-e2e-rnnt"
# Сервис отклонил запись или подсказки: повтор не поможет (спека #17, раздел 5).
_REJECTED_STATUSES = frozenset({413, 415, 422})


class Transcript(BaseModel):
    """Текст записи и её длительность в секундах."""

    model_config = ConfigDict(frozen=True)

    text: str
    duration: float


class AsrRejectedError(AiError):
    """Сервис ASR отклонил запись: слишком большая, не тот формат, не читается, плохая подсказка.

    `detail` сервиса не содержит ни текста записи, ни подсказок.
    """

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"Сервис ASR отклонил запись ({status}): {detail}")
        self.status = status
        self.detail = detail


class AsrClient:
    """Распознавание записей на узлах ASR по порядку, с записью каждого вызова в ai_runs."""

    def __init__(
        self, urls: Sequence[str], recorder: RunRecorder, *, timeout: httpx2.Timeout
    ) -> None:
        self._urls = tuple(urls)
        self._recorder = recorder
        self._timeout = timeout

    async def transcribe(
        self,
        audio: bytes,
        *,
        filename: str,
        content_type: str,
        hotwords: Sequence[str] = (),
        task: str,
    ) -> AiResult[Transcript]:
        """Распознаёт запись webm/opus или mp4/aac.

        Args:
            audio: Байты записи.
            filename: Имя файла для multipart.
            content_type: Тип записи, например `audio/webm`.
            hotwords: Подсказки — названия препаратов, по одному слову, в нужном регистре.
            task: Имя задачи для ai_runs, например `diary.transcribe`.

        Raises:
            AsrRejectedError: Сервис отклонил запись или подсказки (413, 415, 422).
            AiServiceError: Узел ответил другой ошибкой.
            NodesUnavailableError: Ни один узел ASR не принял запрос.
            AiNoAnswerError: Узел принял запрос, но не ответил: таймаут ответа или обрыв.
            AiOutputError: Ответ сервиса не по схеме `Transcript`.
        """
        async with (
            track_run(
                self._recorder,
                role="asr",
                task=task,
                model=MODEL_NAME,
                input_hash=hash_input(audio),
                params={"hotwords": len(hotwords), "decoding": "beam" if hotwords else "greedy"},
            ) as run,
            httpx2.AsyncClient(timeout=self._timeout) as client,
        ):
            for url in self._urls:
                try:
                    response = await client.post(
                        f"{url}/v1/audio/transcriptions",
                        files={"file": (filename, audio, content_type)},
                        data={"hotwords": list(hotwords)} if hotwords else None,
                    )
                    response.raise_for_status()
                except httpx2.HTTPStatusError as exc:
                    if is_unavailable(exc):
                        report_unavailable("asr", url, exc)
                        continue
                    # from None: в цепочке был бы ответ узла с телом.
                    status = exc.response.status_code
                    if status in _REJECTED_STATUSES:
                        raise AsrRejectedError(status, _detail(exc.response)) from None
                    raise AiServiceError("asr", status) from None
                except httpx2.TransportError as exc:
                    if is_unavailable(exc):
                        report_unavailable("asr", url, exc)
                        continue
                    # Таймаут ответа или обрыв: запрос мог дойти до модели.
                    raise AiNoAnswerError("asr") from None
                try:
                    transcript = Transcript.model_validate(response.json())
                except (json.JSONDecodeError, UnicodeDecodeError, ValidationError):
                    # Ошибка pydantic повторяет вход в input_value, а там текст записи.
                    raise AiOutputError("asr") from None
                run.node = url
                run.audio_seconds = transcript.duration
                break
            else:
                raise NodesUnavailableError("asr")
        if run.run_id is None:
            # track_run выставляет id при выходе без ошибки; без него результат не отдаём.
            raise RuntimeError("track_run не выставил id строки ai_runs")
        return AiResult(output=transcript, run_id=run.run_id)


def _detail(response: httpx2.Response) -> str:
    try:
        detail = response.json().get("detail", "")
    except (json.JSONDecodeError, AttributeError):
        return ""
    return detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)


def build_asr_client(settings: Settings, recorder: RunRecorder) -> AsrClient:
    """Собирает клиент ASR из узлов `COMPASS_ASR_URLS`.

    Raises:
        AiNotConfiguredError: Адреса узлов ASR не заданы.
    """
    timeout = httpx2.Timeout(
        settings.asr_timeout_seconds, connect=settings.ai_connect_timeout_seconds
    )
    return AsrClient(node_urls(settings, "asr"), recorder, timeout=timeout)


__all__ = ["MODEL_NAME", "AsrClient", "AsrRejectedError", "Transcript", "build_asr_client"]
