"""Проверка загруженного файла и картинка → PDF (спека #30, разделы 2 и 3.2)."""

import io

import pytest
from PIL import Image

from clin_compass.modules.ingestion import files
from tests.ingestion.samples import image, layer_texts, page_sizes, pdf


@pytest.mark.parametrize(
    ("data", "content_type"),
    [
        (pdf(), "application/pdf"),
        (image("PNG"), "image/png"),
        (image("JPEG"), "image/jpeg"),
    ],
)
def test_type_comes_from_signature(data: bytes, content_type: str) -> None:
    assert files.inspect(data).content_type == content_type


@pytest.mark.parametrize(
    "data",
    [b"", b"GIF89a....", b"<html>%PDF-</html>", b"PK\x03\x04docx"],
)
def test_other_files_are_unsupported(data: bytes) -> None:
    with pytest.raises(files.UnsupportedDocumentError):
        files.inspect(data)


def test_pdf_pages_are_counted() -> None:
    assert files.inspect(pdf(6)).pages == 6


def test_seven_pages_are_too_many() -> None:
    with pytest.raises(files.TooManyPagesError):
        files.inspect(pdf(7))


@pytest.mark.parametrize("data", [b"%PDF-1.7\nnot a pdf", pdf()[:200]])
def test_broken_pdf_is_unreadable(data: bytes) -> None:
    with pytest.raises(files.UnreadableDocumentError):
        files.inspect(data)


def test_broken_image_is_unreadable() -> None:
    with pytest.raises(files.UnreadableDocumentError):
        files.inspect(image("PNG", (400, 400))[:120])


def test_image_is_one_page() -> None:
    assert files.inspect(image("JPEG")).pages == 1


def test_png_becomes_one_page_pdf_without_layer() -> None:
    converted = files.to_pdf(image("PNG", (40, 20)), "image/png")

    assert page_sizes(converted) == [(40.0, 20.0)]
    assert layer_texts(converted) == [""]


def test_jpeg_with_exif_rotation_becomes_upright_page() -> None:
    # Фото с телефона: пиксели лежат боком, метка EXIF говорит повернуть на 90°.
    converted = files.to_pdf(image("JPEG", (40, 20), orientation=6), "image/jpeg")

    assert page_sizes(converted) == [(20.0, 40.0)]


def test_pdf_is_passed_as_is() -> None:
    original = pdf(2)

    assert files.to_pdf(original, "application/pdf") is original


def test_image_over_40_megapixels_is_refused_before_decoding() -> None:
    # PNG в несколько килобайт с размером 8000 × 6000: распаковка заняла бы сотни мегабайт.
    picture = Image.new("1", (8000, 6000))
    buffer = io.BytesIO()
    picture.save(buffer, "PNG")

    with pytest.raises(files.UnreadableDocumentError, match="too large"):
        files.inspect(buffer.getvalue())


def test_big_image_is_downscaled_to_2576_px() -> None:
    converted = files.to_pdf(image("PNG", (4000, 3000)), "image/png")

    assert page_sizes(converted) == [(2576.0, 1932.0)]


def test_big_rotated_photo_is_downscaled_then_turned() -> None:
    converted = files.to_pdf(image("JPEG", (4000, 2000), orientation=6), "image/jpeg")

    assert page_sizes(converted) == [(1288.0, 2576.0)]
