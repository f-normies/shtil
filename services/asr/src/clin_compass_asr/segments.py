"""Нарезка записи на сегменты для GigaAM (спека #17, раздел 6, шаг 3).

GigaAM рассчитан на фрагменты до 25 с. Запись до 20 с идёт целиком: нарезка ей не нужна,
а VAD мог бы отрезать тихую речь. Длиннее — режет Silero VAD с параметрами ADR-0007.

`max_speech_duration` у VAD sherpa-onnx мягкий: после 20 с он только ищет короткую паузу.
В сплошной речи (голос на фоне телевизора) паузы нет, и сегмент выходит любой длины. На 100 с
это +0,9 ГБ памяти, на 300 с модель падает. Поэтому сегменты длиннее 20 с режутся ещё раз.
"""

import itertools
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import numpy.typing as npt
import sherpa_onnx

from clin_compass_asr.audio import SAMPLE_RATE

# Запись до 20 с идёт целиком; сегмент речи не длиннее 20 с.
MAX_SEGMENT_SAMPLES = 20 * SAMPLE_RATE
# ADR-0007: пауза 0,5 с, сегмент не длиннее 20 с, поля 0,3 с. Без полей теряется первое слово.
MIN_SILENCE_SECONDS = 0.5
MAX_SPEECH_SECONDS = 20.0
PAD_SAMPLES = int(0.3 * SAMPLE_RATE)


def pad_spans(spans: Sequence[tuple[int, int]], *, total: int, pad: int) -> list[tuple[int, int]]:
    """Расширяет сегменты речи на `pad` отсчётов с обеих сторон.

    Поле не заходит дальше границы речи соседнего сегмента и границ записи. Когда VAD режет
    сплошную речь на 20 с, сегменты стоят встык, и поле на стыке не добавляется: иначе слово
    попало бы в оба сегмента.

    Args:
        spans: Сегменты речи `(начало, конец)` в отсчётах, по возрастанию.
        total: Длина записи в отсчётах.
        pad: Ширина поля в отсчётах.

    Returns:
        Сегменты с полями, в том же порядке.
    """
    padded = []
    for i, (start, end) in enumerate(spans):
        prev_end = spans[i - 1][1] if i > 0 else 0
        next_start = spans[i + 1][0] if i + 1 < len(spans) else total
        padded.append((max(start - pad, prev_end), min(end + pad, next_start)))
    return padded


def cap_spans(spans: Sequence[tuple[int, int]], *, max_len: int) -> list[tuple[int, int]]:
    """Режет сегменты длиннее `max_len` на равные части встык.

    Части стоят встык, поэтому `pad_spans` на стыках полей не добавляет. Разрез может пройти
    по слову: это лучше, чем сегмент, на котором модель не справится.

    Args:
        spans: Сегменты речи `(начало, конец)` в отсчётах, по возрастанию.
        max_len: Наибольшая длина сегмента в отсчётах.

    Returns:
        Сегменты не длиннее `max_len`, в том же порядке.
    """
    capped: list[tuple[int, int]] = []
    for start, end in spans:
        pieces = -(-(end - start) // max_len)  # деление с округлением вверх
        bounds = [start + (end - start) * i // pieces for i in range(pieces + 1)]
        capped.extend(itertools.pairwise(bounds))
    return capped


class Segmenter:
    """Режет запись на сегменты речи. Модель VAD читается только для записей длиннее 20 с."""

    def __init__(self, vad_model: Path) -> None:
        self._vad_model = vad_model

    def split(self, samples: npt.NDArray[np.float32]) -> list[npt.NDArray[np.float32]]:
        """Сегменты записи: вся запись, если она не длиннее 20 с, иначе речь по VAD с полями.

        Args:
            samples: Отсчёты float32, 16 кГц, моно.

        Returns:
            Сегменты по порядку, каждый не длиннее 20 с и полей; пустой список,
            если VAD не нашёл речи.
        """
        if len(samples) <= MAX_SEGMENT_SAMPLES:
            return [samples]
        spans = cap_spans(self._detect_speech(samples), max_len=MAX_SEGMENT_SAMPLES)
        padded = pad_spans(spans, total=len(samples), pad=PAD_SAMPLES)
        return [samples[start:end] for start, end in padded]

    def _detect_speech(self, samples: npt.NDArray[np.float32]) -> list[tuple[int, int]]:
        config = sherpa_onnx.VadModelConfig()
        config.silero_vad.model = str(self._vad_model)
        config.silero_vad.min_silence_duration = MIN_SILENCE_SECONDS
        config.silero_vad.max_speech_duration = MAX_SPEECH_SECONDS
        config.sample_rate = SAMPLE_RATE
        # Детектор хранит отсчёты в буфере: его хватает на всю запись.
        vad = sherpa_onnx.VoiceActivityDetector(
            config, buffer_size_in_seconds=len(samples) / SAMPLE_RATE + 1
        )
        window = config.silero_vad.window_size
        spans: list[tuple[int, int]] = []

        def take_ready() -> None:
            while not vad.empty():
                segment = vad.front
                spans.append((segment.start, segment.start + len(segment.samples)))
                vad.pop()

        for offset in range(0, len(samples), window):
            vad.accept_waveform(samples[offset : offset + window])
            take_ready()
        vad.flush()
        take_ready()
        return spans
