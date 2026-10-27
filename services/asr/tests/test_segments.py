"""Нарезка записи: поля сегментов и запись до 20 с целиком (спека #17, раздел 6, шаг 3)."""

from pathlib import Path

import numpy as np
import pytest

from clin_compass_asr.segments import Segmenter, cap_spans, pad_spans

PAD = 4_800  # 0,3 с при 16 кГц


@pytest.mark.parametrize(
    ("spans", "total", "want"),
    [
        pytest.param([], 64_000, [], id="no-speech"),
        pytest.param([(16_000, 32_000)], 64_000, [(11_200, 36_800)], id="pad-into-silence"),
        pytest.param([(1_000, 63_000)], 64_000, [(0, 64_000)], id="clamp-to-edges"),
        # Пауза 0,5 с между сегментами: поля доходят до 0,3 с, в речь соседа не заходят.
        pytest.param(
            [(0, 16_000), (24_000, 40_000)],
            64_000,
            [(0, 20_800), (19_200, 44_800)],
            id="shared-pause",
        ),
        # VAD разрезал сплошную речь на 20 с: на стыке поле не добавляется.
        pytest.param(
            [(0, 320_000), (320_000, 400_000)],
            400_000,
            [(0, 320_000), (320_000, 400_000)],
            id="adjacent",
        ),
        pytest.param(
            [(10_000, 20_000), (22_000, 30_000)],
            64_000,
            [(5_200, 22_000), (20_000, 34_800)],
            id="pause-shorter-than-pad",
        ),
    ],
)
def test_pad_spans(spans: list[tuple[int, int]], total: int, want: list[tuple[int, int]]) -> None:
    assert pad_spans(spans, total=total, pad=PAD) == want


MAX = 320_000  # 20 с при 16 кГц


@pytest.mark.parametrize(
    ("spans", "want"),
    [
        pytest.param([(0, 320_000)], [(0, 320_000)], id="exactly-20s"),
        # 51 с сплошной речи: VAD не нашёл паузы, части равные и встык.
        pytest.param(
            [(16_000, 832_000)],
            [(16_000, 288_000), (288_000, 560_000), (560_000, 832_000)],
            id="51s-into-3",
        ),
        pytest.param([(0, 320_001)], [(0, 160_000), (160_000, 320_001)], id="20s-plus-1"),
        pytest.param(
            [(0, 100_000), (200_000, 600_000)],
            [(0, 100_000), (200_000, 400_000), (400_000, 600_000)],
            id="only-long-span-cut",
        ),
    ],
)
def test_cap_spans(spans: list[tuple[int, int]], want: list[tuple[int, int]]) -> None:
    assert cap_spans(spans, max_len=MAX) == want


def test_recording_up_to_20_seconds_is_one_segment_without_vad() -> None:
    samples = np.linspace(-0.5, 0.5, 20 * 16_000, dtype=np.float32)
    # Модели VAD нет: если бы короткая запись шла через VAD, split упал бы.
    segmenter = Segmenter(vad_model=Path("/nonexistent/silero_vad.onnx"))

    segments = segmenter.split(samples)

    assert len(segments) == 1
    np.testing.assert_array_equal(segments[0], samples)
