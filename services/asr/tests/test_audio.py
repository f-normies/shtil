"""Декодирование записи в float32, 16 кГц, моно (спека #17, раздел 6, шаг 2)."""

from collections.abc import Callable

import numpy as np
import pytest

from clin_compass_asr.audio import (
    AudioTooLongError,
    UnreadableAudioError,
    UnsupportedAudioError,
    decode,
)
from tests.media import dominant_freq, encode, encode_video_only, encode_wav, sine

# Кодеки с потерями добавляют задержку и выравнивание по кадрам: допуск по длине — 30 мс.
LENGTH_TOLERANCE = 480


@pytest.mark.parametrize(
    ("container", "codec", "rate", "channels"),
    [
        ("webm", "libopus", 48_000, 2),  # Chrome и Android: MediaRecorder audio/webm;codecs=opus
        ("mp4", "aac", 22_050, 1),  # Safari и iOS: audio/mp4
    ],
)
def test_decodes_to_16k_mono_float32(container: str, codec: str, rate: int, channels: int) -> None:
    data = encode(sine(1.0, rate), rate=rate, container=container, codec=codec, channels=channels)

    samples = decode(data)

    assert samples.dtype == np.float32
    assert samples.ndim == 1
    assert abs(len(samples) - 16_000) <= LENGTH_TOLERANCE
    # Без пересэмплирования в 16 кГц пик сместился бы: 440 Гц × 48000/16000 = 1320 Гц.
    assert dominant_freq(samples, 16_000) == pytest.approx(440, abs=5)
    assert float(np.sqrt(np.mean(samples**2))) > 0.1


def _with_zeroed_fmt(wav: bytes) -> bytes:
    """WAV с обнулёнными полями блока fmt (байты 20–35)."""
    return wav[:20] + bytes(16) + wav[36:]


@pytest.mark.parametrize(
    "data_factory",
    [
        lambda: encode_wav(sine(0.5, 16_000), rate=16_000),
        lambda: encode(sine(0.5, 48_000), rate=48_000, container="ogg", codec="libopus"),
        # Блок fmt обнулён. Без format_whitelist его разбирал бы демультиплексор wav
        # и падал на нём; со списком файл отклонён до разбора заголовка.
        lambda: _with_zeroed_fmt(encode_wav(sine(0.5, 16_000), rate=16_000)),
    ],
    ids=["wav-pcm", "ogg-opus", "wav-broken-header"],
)
def test_rejects_other_containers_before_parsing(data_factory: Callable[[], bytes]) -> None:
    with pytest.raises(UnsupportedAudioError):
        decode(data_factory())


@pytest.mark.parametrize(
    ("data_factory", "container", "codec"),
    [
        (
            lambda: encode(sine(0.5, 48_000), rate=48_000, container="matroska", codec="flac"),
            "matroska,webm",
            "flac",
        ),
        (
            lambda: encode(sine(0.5, 48_000), rate=48_000, container="mp4", codec="libopus"),
            "mov,mp4,m4a,3gp,3g2,mj2",
            "opus",
        ),
    ],
    ids=["matroska-flac", "mp4-opus"],
)
def test_rejects_other_codec_in_allowed_container(
    data_factory: Callable[[], bytes], container: str, codec: str
) -> None:
    with pytest.raises(UnsupportedAudioError) as exc_info:
        decode(data_factory())

    assert (exc_info.value.container, exc_info.value.codec) == (container, codec)


def test_rejects_garbage() -> None:
    with pytest.raises(UnreadableAudioError):
        decode(b"\x00\x01not an audio file" * 100)


def test_rejects_file_without_audio_track() -> None:
    with pytest.raises(UnreadableAudioError):
        decode(encode_video_only())


def test_stops_on_too_long_recording() -> None:
    data = encode(sine(3.0, 48_000), rate=48_000, container="webm", codec="libopus")

    with pytest.raises(AudioTooLongError):
        decode(data, max_seconds=2.0)


def test_accepts_recording_within_limit() -> None:
    data = encode(sine(1.5, 48_000), rate=48_000, container="webm", codec="libopus")

    assert abs(len(decode(data, max_seconds=2.0)) - 24_000) <= LENGTH_TOLERANCE
