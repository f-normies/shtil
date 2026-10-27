"""Подсказки и выбор распознавателя (спека #17, раздел 6, шаги 4–5)."""

from pathlib import Path

import numpy as np
import numpy.typing as npt
import pytest

from clin_compass_asr.recognizer import (
    InvalidHotwordError,
    ModelFilesMissingError,
    Transcriber,
    check_hotwords,
    vocabulary_chars,
)
from tests.fakes import VOCAB, FakeRecognizer


def test_vocabulary_chars_skip_service_tokens_and_word_boundary(tmp_path: Path) -> None:
    tokens = tmp_path / "tokens.txt"
    tokens.write_text("<unk> 0\n. 1\n▁ 2\nе 3\n▁на 4\nL 5\n<blk> 6\n", encoding="utf-8")

    assert vocabulary_chars(tokens) == frozenset({".", "е", "н", "а", "L"})


@pytest.mark.parametrize(
    "words",
    [
        pytest.param([], id="empty-list"),
        pytest.param(["Авиандр"], id="trade-name"),
        pytest.param(["Ко-тримоксазол", "L-тироксин", "B12"], id="hyphen-latin-digits"),
        pytest.param(["а" * 64], id="64-chars"),
        pytest.param(["Авиандр"] * 200, id="200-words"),
    ],
)
def test_check_hotwords_accepts(words: list[str]) -> None:
    assert check_hotwords(words, VOCAB) == words


@pytest.mark.parametrize(
    ("words", "index"),
    [
        # Подсказка из нескольких слов съедает соседние слова: «Авиандр ретард» → пропала доза.
        pytest.param(["Авиандр", "Авиандр ретард"], 1, id="space"),
        pytest.param(["Авиандр/Сертралин"], 0, id="slash-splits-hotwords"),
        pytest.param([":2.5"], 0, id="colon-is-score"),
        pytest.param(["Авиандр:3"], 0, id="colon-inside"),
        pytest.param(["Авиандр."], 0, id="punctuation"),
        pytest.param([""], 0, id="empty-word"),
        pytest.param(["а" * 65], 0, id="65-chars"),
        # Заглавной Ъ в словаре нет: sherpa-onnx выбросил бы все подсказки и напечатал их в stderr.
        pytest.param(["СЪЕЗД"], 0, id="char-outside-vocab"),
        pytest.param(["Авиандр\n"], 0, id="newline"),
    ],
)
def test_check_hotwords_rejects(words: list[str], index: int) -> None:
    with pytest.raises(InvalidHotwordError) as exc_info:
        check_hotwords(words, VOCAB)

    assert exc_info.value.index == index
    # Слово — назначение пациента: в тексте ошибки его нет.
    assert words[index] not in str(exc_info.value) or words[index] == ""


def test_check_hotwords_rejects_more_than_200() -> None:
    with pytest.raises(InvalidHotwordError):
        check_hotwords(["Авиандр"] * 201, VOCAB)


def _segments(n: int) -> list[npt.NDArray[np.float32]]:
    return [np.full(16_000, i, dtype=np.float32) for i in range(n)]


def _transcriber(
    greedy: FakeRecognizer, beam: FakeRecognizer, segments: list[npt.NDArray[np.float32]]
) -> Transcriber:
    return Transcriber(
        greedy=greedy, beam=beam, split=lambda _samples: segments, allowed_chars=VOCAB
    )


def test_without_hotwords_uses_greedy() -> None:
    greedy = FakeRecognizer(["Сон стал лучше."])
    beam = FakeRecognizer(["не должен вызываться"])
    segments = _segments(1)

    transcript = _transcriber(greedy, beam, segments).transcribe(segments[0], [])

    assert transcript.text == "Сон стал лучше."
    assert transcript.decoding == "greedy_search"
    assert [s.hotwords for s in greedy.streams] == [None]
    assert beam.streams == []


def test_with_hotwords_uses_beam_with_slash_joined_words() -> None:
    greedy = FakeRecognizer(["не должен вызываться"])
    beam = FakeRecognizer(["Принимаю Авиандр.", "Сертралин отменили."])
    segments = _segments(2)

    transcript = _transcriber(greedy, beam, segments).transcribe(
        np.zeros(1, dtype=np.float32), ["Авиандр", "Сертралин"]
    )

    assert transcript.text == "Принимаю Авиандр. Сертралин отменили."
    assert transcript.decoding == "modified_beam_search"
    assert [s.hotwords for s in beam.streams] == ["Авиандр/Сертралин", "Авиандр/Сертралин"]
    assert greedy.streams == []


def test_segments_decoded_in_one_batch_in_order() -> None:
    greedy = FakeRecognizer(["Первая фраза.", "", "Вторая фраза."])
    segments = _segments(3)

    transcript = _transcriber(greedy, FakeRecognizer([]), segments).transcribe(segments[0], [])

    assert greedy.decode_calls == 1
    assert [float(s.samples[0]) for s in greedy.streams if s.samples is not None] == [0, 1, 2]
    # Пустой текст сегмента не даёт двойного пробела.
    assert transcript.text == "Первая фраза. Вторая фраза."
    assert transcript.segments == 3


def test_no_speech_gives_empty_text_without_decoding() -> None:
    greedy = FakeRecognizer([])

    transcript = _transcriber(greedy, FakeRecognizer([]), []).transcribe(
        np.zeros(400_000, dtype=np.float32), []
    )

    assert transcript.text == ""
    assert transcript.segments == 0
    assert greedy.decode_calls == 0


def test_load_names_missing_model_files(tmp_path: Path) -> None:
    (tmp_path / "tokens.txt").write_text("<unk> 0\n", encoding="utf-8")
    (tmp_path / "encoder.int8.onnx").write_bytes(b"")

    with pytest.raises(ModelFilesMissingError) as exc_info:
        Transcriber.load(tmp_path, num_threads=1)

    assert exc_info.value.missing == [
        "decoder.onnx",
        "joiner.onnx",
        "bpe.vocab",
        "silero_vad.onnx",
    ]


def test_segments_shorter_than_100_ms_are_skipped() -> None:
    # Модель падает на входе короче 80 отсчётов; в 0,1 с слова нет.
    greedy = FakeRecognizer(["Сон стал лучше."])
    segments = [np.zeros(n, dtype=np.float32) for n in (0, 1, 1_599, 16_000)]

    transcript = _transcriber(greedy, FakeRecognizer([]), segments).transcribe(segments[3], [])

    assert [len(s.samples) for s in greedy.streams if s.samples is not None] == [16_000]
    assert transcript.text == "Сон стал лучше."
    assert transcript.segments == 1


def test_only_tiny_segments_give_empty_text_without_decoding() -> None:
    greedy = FakeRecognizer([])
    tiny = [np.zeros(30, dtype=np.float32)]

    transcript = _transcriber(greedy, FakeRecognizer([]), tiny).transcribe(tiny[0], [])

    assert transcript.text == ""
    assert greedy.decode_calls == 0


def test_check_hotwords_composes_decomposed_letters() -> None:
    # «й» и «Ё» из двух кодовых точек: и + U+0306, Е + U+0308. Так их пишут файлы macOS.
    decomposed = ["Праи\u0306м", "Е\u0308ж"]

    assert check_hotwords(decomposed, VOCAB) == ["Прайм", "Ёж"]
