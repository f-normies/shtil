"""Декодирование голосовой записи в float32, 16 кГц, моно (спека #17, раздел 6, шаг 2).

FFmpeg из колеса PyAV работает в процессе сервиса. Чтобы повреждённый файл редкого формата
не обрушил сервис, разбираются только пары контейнер — кодек из браузерного MediaRecorder:
webm с opus (Chrome, Android) и mp4 с aac (Safari, iOS). Файл другого формата FFmpeg
отклоняет до разбора заголовка (`format_whitelist`), кодек сверяется после открытия.
"""

import io
from collections.abc import Iterator

import av
import numpy as np
import numpy.typing as npt
from av.audio.stream import AudioStream
from av.container import InputContainer

SAMPLE_RATE = 16_000
MAX_DURATION_SECONDS = 600.0

# Демультиплексоры, которым FFmpeg разрешено открыть файл: matroska читает webm, mov — mp4.
_DEMUXERS = "matroska,mov"
# Имя демультиплексора FFmpeg (format.name) и имя декодера.
_ALLOWED = frozenset({("matroska,webm", "opus"), ("mov,mp4,m4a,3gp,3g2,mj2", "aac")})
_EXPECTED = "нужен webm/opus или mp4/aac"


class AudioError(Exception):
    """Запись нельзя распознать. Текст ошибки не содержит данных записи."""


class UnsupportedAudioError(AudioError):
    """Пара контейнер — кодек не из списка. Без имён — FFmpeg отклонил формат до разбора."""

    def __init__(self, container: str | None = None, codec: str | None = None) -> None:
        found = f"{container}/{codec}; " if container else ""
        super().__init__(f"формат не поддерживается: {found}{_EXPECTED}")
        self.container = container
        self.codec = codec


class UnreadableAudioError(AudioError):
    """FFmpeg не смог прочитать файл, или в нём нет звуковой дорожки."""


class AudioTooLongError(AudioError):
    """Запись длиннее лимита."""


def decode(data: bytes, *, max_seconds: float = MAX_DURATION_SECONDS) -> npt.NDArray[np.float32]:
    """Декодирует запись в отсчёты float32, 16 кГц, моно.

    Args:
        data: Содержимое файла.
        max_seconds: Лимит длительности. Декодирование останавливается, как только он превышен.

    Returns:
        Одномерный массив отсчётов; пустой, если в дорожке нет звука.

    Raises:
        UnsupportedAudioError: Контейнер или кодек не из списка.
        UnreadableAudioError: Файл не читается, или в нём нет звуковой дорожки.
        AudioTooLongError: Запись длиннее `max_seconds`.
    """
    max_samples = int(max_seconds * SAMPLE_RATE)
    chunks: list[npt.NDArray[np.float32]] = []
    total = 0
    try:
        with _open(data) as container:
            for chunk in _resampled(container):
                total += len(chunk)
                if total > max_samples:
                    raise AudioTooLongError(f"запись длиннее {max_seconds:g} с")
                chunks.append(chunk)
    except av.FFmpegError as exc:
        raise UnreadableAudioError("файл не читается") from exc
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks)


def _open(data: bytes) -> InputContainer:
    """Открывает файл только демультиплексорами matroska и mov."""
    try:
        return av.open(io.BytesIO(data), mode="r", options={"format_whitelist": _DEMUXERS})
    except av.ArgumentError as exc:
        # Так FFmpeg отвечает на формат вне format_whitelist; мусорные байты — InvalidDataError.
        raise UnsupportedAudioError() from exc


def _resampled(container: InputContainer) -> Iterator[npt.NDArray[np.float32]]:
    """Отсчёты первой звуковой дорожки после пересэмплирования, кусками по кадрам."""
    if not container.streams.audio:
        raise UnreadableAudioError("в файле нет звуковой дорожки")
    stream: AudioStream = container.streams.audio[0]
    pair = (container.format.name, stream.codec_context.name)
    if pair not in _ALLOWED:
        raise UnsupportedAudioError(*pair)

    resampler = av.AudioResampler(format="flt", layout="mono", rate=SAMPLE_RATE)
    for frame in container.decode(stream):
        for out in resampler.resample(frame):
            yield out.to_ndarray().reshape(-1).astype(np.float32, copy=False)
    # Хвост, который пересэмплер держит в буфере.
    for out in resampler.resample(None):
        yield out.to_ndarray().reshape(-1).astype(np.float32, copy=False)
