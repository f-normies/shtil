"""Заглушки sherpa-onnx для тестов без модели."""

import threading
import time
from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

# Символы словаря GigaAM-v3 e2e_rnnt (tokens.txt без <unk>, <blk> и ▁): кириллица без заглавных
# Ъ и Ь, латиница, цифры, знаки препинания.
CYRILLIC = "ЁАБВГДЕЖЗИЙКЛМНОПРСТУФХЦЧШЩЫЭЮЯабвгдежзийклмнопрстуфхцчшщъыьэюяё"
LATIN = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
VOCAB = frozenset(CYRILLIC + LATIN + "0123456789" + "!\"$%&'+,-./:;<>?«°»—€₽")


@dataclass
class FakeResult:
    text: str


@dataclass
class FakeStream:
    hotwords: str | None
    result: FakeResult = field(default_factory=lambda: FakeResult(""))
    samples: npt.NDArray[np.float32] | None = None

    def accept_waveform(self, sample_rate: int, samples: npt.NDArray[np.float32]) -> None:
        assert sample_rate == 16_000
        self.samples = samples


class FakeRecognizer:
    """Повторяет нужную часть sherpa_onnx.OfflineRecognizer: create_stream и decode_streams."""

    def __init__(self, texts: list[str], *, delay: float = 0.0) -> None:
        self._texts = texts
        self._delay = delay
        self._lock = threading.Lock()
        self._active = 0
        self.streams: list[FakeStream] = []
        self.decode_calls = 0
        self.max_active = 0

    def create_stream(self, hotwords: str | None = None) -> FakeStream:
        stream = FakeStream(hotwords)
        self.streams.append(stream)
        return stream

    def decode_streams(self, streams: list[FakeStream]) -> None:
        with self._lock:
            self.decode_calls += 1
            self._active += 1
            self.max_active = max(self.max_active, self._active)
        time.sleep(self._delay)
        for stream, text in zip(streams, self._texts, strict=False):
            stream.result = FakeResult(text)
        with self._lock:
            self._active -= 1
