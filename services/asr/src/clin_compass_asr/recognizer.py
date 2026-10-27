"""Распознавание GigaAM-v3 `e2e_rnnt` в sherpa-onnx с подсказками и без (спека #17, раздел 6).

Способ декодирования задаётся при создании распознавателя, поэтому их два (ADR-0007):

- `greedy_search` — без подсказок. Beam search без подсказок молча выбрасывает незнакомое
  название препарата целиком, greedy оставляет хотя бы обрывок слова;
- `modified_beam_search` — с подсказками, вес 2,0. При весе выше 2,5 появляются ложные вставки.
"""

import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
import numpy.typing as npt
import sherpa_onnx

from clin_compass_asr.audio import SAMPLE_RATE
from clin_compass_asr.segments import Segmenter

HOTWORDS_SCORE = 2.0
# Модель падает на входе короче 80 отсчётов (ConvInteger в ONNX Runtime); в 0,1 с слова нет.
MIN_SEGMENT_SAMPLES = SAMPLE_RATE // 10
MAX_HOTWORDS = 200
MAX_HOTWORD_LENGTH = 64

GREEDY = "greedy_search"
BEAM = "modified_beam_search"

# Файлы в ASR_MODEL_DIR. Модель — из архива релиза; bpe.vocab и silero_vad.onnx кладёт fetch.sh.
ENCODER = "encoder.int8.onnx"
DECODER = "decoder.onnx"
JOINER = "joiner.onnx"
TOKENS = "tokens.txt"
BPE_VOCAB = "bpe.vocab"
VAD = "silero_vad.onnx"
MODEL_FILES = (ENCODER, DECODER, JOINER, TOKENS, BPE_VOCAB, VAD)

Samples = npt.NDArray[np.float32]


class InvalidHotwordError(ValueError):
    """Подсказка не по правилам. В тексте ошибки — номер подсказки, само слово не повторяется."""

    def __init__(self, index: int, reason: str) -> None:
        super().__init__(f"подсказка №{index + 1}: {reason}")
        self.index = index


class ModelFilesMissingError(RuntimeError):
    """В каталоге модели не хватает файлов."""

    def __init__(self, model_dir: Path, missing: list[str]) -> None:
        super().__init__(
            f"в {model_dir} нет файлов модели: {', '.join(missing)}. "
            "Запустите services/asr/fetch.sh."
        )
        self.missing = missing


def vocabulary_chars(tokens_file: Path) -> frozenset[str]:
    """Символы, из которых состоят токены модели, без служебных токенов и знака начала слова.

    Args:
        tokens_file: `tokens.txt` модели, строки вида `<токен> <номер>`.

    Returns:
        Набор символов, которые модель может распознать в подсказке.
    """
    chars: set[str] = set()
    for line in tokens_file.read_text(encoding="utf-8").splitlines():
        token = line.rsplit(maxsplit=1)[0]
        if len(token) > 2 and token.startswith("<") and token.endswith(">"):
            continue  # <unk>, <blk>
        chars.update(token.replace("▁", ""))
    return frozenset(chars)


def check_hotwords(words: Sequence[str], allowed_chars: frozenset[str]) -> list[str]:
    """Проверяет подсказки до того, как они попадут в sherpa-onnx.

    sherpa-onnx склеивает подсказки через `/`, а слово с `:` в начале считает весом. Подсказку,
    которую нельзя разложить на токены, он молча отбрасывает вместе с остальными и печатает
    в stderr. Подсказка из нескольких слов съедает соседние слова текста. Поэтому подсказка —
    одно слово из букв, цифр и дефиса, и каждый символ есть в словаре модели.

    Args:
        words: Подсказки из запроса. Регистр сохраняется: он переходит в текст.
        allowed_chars: Символы словаря модели (`vocabulary_chars`).

    Returns:
        Подсказки в форме NFC: «й» и «ё» из двух кодовых точек (так пишут файлы macOS)
        становятся одной, как в словаре модели.

    Raises:
        InvalidHotwordError: Подсказок больше 200 или подсказка не по правилам.
    """
    if len(words) > MAX_HOTWORDS:
        raise InvalidHotwordError(MAX_HOTWORDS, f"подсказок больше {MAX_HOTWORDS}")
    composed = [unicodedata.normalize("NFC", word) for word in words]
    for index, word in enumerate(composed):
        if not 1 <= len(word) <= MAX_HOTWORD_LENGTH:
            raise InvalidHotwordError(index, f"длина не от 1 до {MAX_HOTWORD_LENGTH} символов")
        if not all(char.isalnum() or char == "-" for char in word):
            raise InvalidHotwordError(index, "допустимы только буквы, цифры и дефис")
        if not set(word) <= allowed_chars:
            raise InvalidHotwordError(index, "символ вне словаря модели")
    return composed


class _Result(Protocol):
    @property
    def text(self) -> str: ...


class _Stream(Protocol):
    @property
    def result(self) -> _Result: ...

    def accept_waveform(self, sample_rate: int, samples: Samples) -> None: ...


class _Recognizer(Protocol):
    def create_stream(self, hotwords: str | None = None) -> _Stream: ...

    def decode_streams(self, streams: list[_Stream]) -> None: ...


@dataclass(frozen=True)
class Transcript:
    """Текст записи и параметры распознавания для лога."""

    text: str
    segments: int
    decoding: str


class Transcriber:
    """Два распознавателя GigaAM-v3 и нарезка записи. Один экземпляр на процесс."""

    def __init__(
        self,
        *,
        greedy: _Recognizer,
        beam: _Recognizer,
        split: Callable[[Samples], list[Samples]],
        allowed_chars: frozenset[str],
    ) -> None:
        self._greedy = greedy
        self._beam = beam
        self._split = split
        self.allowed_chars = allowed_chars

    @classmethod
    def load(cls, model_dir: Path, *, num_threads: int) -> "Transcriber":
        """Загружает модель и VAD из каталога, который готовит `fetch.sh`.

        Args:
            model_dir: Каталог с файлами `MODEL_FILES`.
            num_threads: Потоки ONNX Runtime на каждый распознаватель.

        Raises:
            ModelFilesMissingError: Каких-то файлов нет.
        """
        missing = [name for name in MODEL_FILES if not (model_dir / name).is_file()]
        if missing:
            raise ModelFilesMissingError(model_dir, missing)

        def recognizer(decoding: str, **options: object) -> _Recognizer:
            recognizer: _Recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
                encoder=str(model_dir / ENCODER),
                decoder=str(model_dir / DECODER),
                joiner=str(model_dir / JOINER),
                tokens=str(model_dir / TOKENS),
                num_threads=num_threads,
                model_type="nemo_transducer",
                decoding_method=decoding,
                **options,
            )
            return recognizer

        return cls(
            greedy=recognizer(GREEDY),
            beam=recognizer(
                BEAM,
                modeling_unit="bpe",
                bpe_vocab=str(model_dir / BPE_VOCAB),
                hotwords_score=HOTWORDS_SCORE,
            ),
            split=Segmenter(model_dir / VAD).split,
            allowed_chars=vocabulary_chars(model_dir / TOKENS),
        )

    def transcribe(self, samples: Samples, hotwords: Sequence[str]) -> Transcript:
        """Распознаёт запись. Подсказки уже проверены `check_hotwords`.

        Args:
            samples: Отсчёты float32, 16 кГц, моно.
            hotwords: Подсказки; пустой список — жадное декодирование.

        Returns:
            Текст сегментов через пробел. Сегменты короче 0,1 с пропускаются.
            Пустой текст, если речи нет.
        """
        recognizer, decoding = (self._beam, BEAM) if hotwords else (self._greedy, GREEDY)
        segments = [s for s in self._split(samples) if len(s) >= MIN_SEGMENT_SAMPLES]
        if not segments:
            return Transcript(text="", segments=0, decoding=decoding)

        joined = "/".join(hotwords)
        streams = []
        for segment in segments:
            stream = (
                recognizer.create_stream(hotwords=joined) if joined else recognizer.create_stream()
            )
            stream.accept_waveform(SAMPLE_RATE, segment)
            streams.append(stream)
        recognizer.decode_streams(streams)
        texts = (stream.result.text.strip() for stream in streams)
        return Transcript(
            text=" ".join(text for text in texts if text),
            segments=len(segments),
            decoding=decoding,
        )
