"""Распознавание на настоящей модели (спека #17, раздел 8). Только локально, на macOS.

Модель — из services/asr/fetch.sh (или ASR_MODEL_DIR), речь синтезирует `say -v Milena`.
Без модели или без `say` тесты пропускаются; в CI их нет.
"""

import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import av
import numpy as np
import numpy.typing as npt
import pytest

from clin_compass_asr import audio
from clin_compass_asr.recognizer import MODEL_FILES, VAD, Transcriber
from clin_compass_asr.segments import PAD_SAMPLES, Segmenter
from tests.media import encode

SERVICE_DIR = Path(__file__).resolve().parents[1]
VOICE = "Milena"


def _model_dir() -> Path:
    if env := os.environ.get("ASR_MODEL_DIR"):
        return Path(env)
    versions = dict(
        line.split("=", 1)
        for line in (SERVICE_DIR / "versions.env").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )
    default_dir = Path.home() / ".cache" / "clin-compass"
    inference_dir = Path(os.environ.get("COMPASS_INFERENCE_DIR", default_dir))
    return inference_dir / "models" / "asr" / versions["MODEL_NAME"]


MODEL_DIR = _model_dir()

pytestmark = [
    pytest.mark.model,
    pytest.mark.skipif(
        not all((MODEL_DIR / name).is_file() for name in MODEL_FILES),
        reason=f"нет модели в {MODEL_DIR}: services/asr/fetch.sh",
    ),
    pytest.mark.skipif(shutil.which("say") is None, reason="нет команды say (только macOS)"),
]


@pytest.fixture(scope="module")
def transcriber() -> Iterator[Transcriber]:
    yield Transcriber.load(MODEL_DIR, num_threads=4)


def _speak(text: str, tmp_path: Path) -> npt.NDArray[np.float32]:
    """Синтезирует фразу в float32, 16 кГц, моно."""
    wav = tmp_path / "phrase.wav"
    subprocess.run(
        ["say", "-v", VOICE, "--file-format=WAVE", "--data-format=LEF32@16000", "-o", wav, text],
        check=True,
    )
    with av.open(str(wav)) as container:
        frames = [f.to_ndarray().reshape(-1) for f in container.decode(audio=0)]
    return np.concatenate(frames).astype(np.float32)


def _webm(samples: npt.NDArray[np.float32]) -> bytes:
    return encode(samples, rate=16_000, container="webm", codec="libopus")


PHRASE = "Доктор, я принимаю Авиандр пятьдесят миллиграмм утром уже две недели."


def test_hotword_brings_drug_name(transcriber: Transcriber, tmp_path: Path) -> None:
    samples = audio.decode(_webm(_speak(PHRASE, tmp_path)))

    transcript = transcriber.transcribe(samples, ["Авиандр"])

    assert "Авиандр" in transcript.text
    # Числа цифрами — признак GigaAM-v3 e2e (ADR-0007).
    assert "50" in transcript.text
    assert transcript.decoding == "modified_beam_search"


def test_greedy_without_hotwords_keeps_punctuation(
    transcriber: Transcriber, tmp_path: Path
) -> None:
    samples = audio.decode(_webm(_speak(PHRASE, tmp_path)))

    transcript = transcriber.transcribe(samples, [])

    assert transcript.decoding == "greedy_search"
    assert transcript.text.startswith("Доктор,")
    assert transcript.text.endswith(".")


LONG_PHRASES = [
    "Сегодня утром настроение было ровным, тревоги почти не было.",
    "Спала около шести часов, просыпалась два раза за ночь.",
    "Днём работала, но к обеду устала сильнее обычного.",
    "Принимаю таблетки каждое утро после завтрака, пропусков не было.",
    "Вечером гуляла в парке около сорока минут.",
    "Аппетит нормальный, тошноты на этой неделе не было.",
    "К вечеру снова появилось беспокойство и заболела голова.",
]


def test_long_recording_goes_through_vad_without_losing_edge_words(
    transcriber: Transcriber, tmp_path: Path
) -> None:
    pause = np.zeros(16_000, dtype=np.float32)
    parts = []
    for phrase in LONG_PHRASES:
        parts += [_speak(phrase, tmp_path), pause]
    samples = audio.decode(_webm(np.concatenate(parts)))
    assert len(samples) > 20 * 16_000

    transcript = transcriber.transcribe(samples, [])

    assert transcript.segments >= 2
    words = transcript.text.replace(",", " ").replace(".", " ").split()
    # Слова на краях сегментов не потеряны. На синтетическом голосе с тишиной в паузах это
    # выполняется и без полей 0,3 с: их расчёт проверяет test_segments, пользу на живой речи — #39.
    first_words = [phrase.split()[0] for phrase in LONG_PHRASES[:-1]]
    assert [word for word in words if word in first_words] == first_words
    assert words[-1] == "голова"


def test_continuous_speech_is_cut_into_segments_up_to_20_seconds(tmp_path: Path) -> None:
    # Фразы встык, без пауз: VAD негде резать, а max_speech_duration сам сегмент не режет.
    # Без своей нарезки выходит сегмент 26 с; на 300 с модель падает с RuntimeError.
    parts = []
    for phrase in LONG_PHRASES * 2:
        speech = _speak(phrase, tmp_path)
        voiced = np.flatnonzero(np.abs(speech) > 0.01)
        parts.append(speech[voiced[0] : voiced[-1]])
    samples = np.concatenate(parts)
    assert len(samples) > 40 * 16_000

    segments = Segmenter(MODEL_DIR / VAD).split(samples)

    assert max(len(segment) for segment in segments) <= 20 * 16_000 + 2 * PAD_SAMPLES


@pytest.mark.parametrize("length", [0, 1, 79])
def test_tiny_recording_gives_empty_text(transcriber: Transcriber, length: int) -> None:
    # Safari: нажали «запись» и сразу «стоп». Модели нужно не меньше 80 отсчётов.
    transcript = transcriber.transcribe(np.zeros(length, dtype=np.float32), [])

    assert transcript.text == ""
