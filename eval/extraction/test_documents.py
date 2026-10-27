"""Файлы `documents/` соответствуют историям `knowledge/cases/` (спека #30, раздел 7).

Если историю поправили, файлы нужно отрисовать заново: `node eval/extraction/render.mjs`.
"""

import hashlib
import json
import re
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from PIL import Image

HERE = Path(__file__).resolve().parent
DOCUMENTS = HERE / "documents"
CASES = HERE.parents[1] / "knowledge" / "cases"
MANIFEST = json.loads((DOCUMENTS / "manifest.json").read_text(encoding="utf-8"))


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower().replace("ё", "е"))


def _layer(path: Path) -> str:
    document = pdfium.PdfDocument(path)
    try:
        texts = []
        for index in range(len(document)):
            textpage = document[index].get_textpage()
            texts.append(textpage.get_text_bounded())
            textpage.close()
        return "\n".join(texts)
    finally:
        document.close()


def test_documents_are_the_four_histories() -> None:
    assert sorted(MANIFEST) == ["case-01", "case-08", "case-12", "case-17"]


@pytest.mark.parametrize("case_id", sorted(MANIFEST))
def test_document_is_rendered_from_current_history(case_id: str) -> None:
    history = (CASES / case_id / "history.md").read_text(encoding="utf-8")

    assert MANIFEST[case_id]["history_sha256"] == hashlib.sha256(history.encode()).hexdigest()


@pytest.mark.parametrize("case_id", sorted(MANIFEST))
def test_pdf_layer_has_every_word_of_history(case_id: str) -> None:
    history = (CASES / case_id / "history.md").read_text(encoding="utf-8")

    layer = _layer(DOCUMENTS / MANIFEST[case_id]["pdf"])

    assert _words(layer) == _words(history)


@pytest.mark.parametrize("case_id", sorted(MANIFEST))
def test_png_is_one_readable_page(case_id: str) -> None:
    with Image.open(DOCUMENTS / MANIFEST[case_id]["png"]) as picture:
        assert picture.format == "PNG"
        assert picture.width >= 1200
        assert picture.height >= picture.width
