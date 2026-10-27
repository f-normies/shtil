"""Проверка загруженного файла и картинка → PDF (спека #30, разделы 2 и 3.2).

Тип определяется по первым байтам файла: заголовку `Content-Type` клиента сервер не верит.
PDF открывает pypdfium2, картинку — Pillow. Картинку больше 40 млн пикселей код отклоняет
по заголовку, до распаковки: PNG в несколько килобайт иначе занял бы сотни мегабайт памяти.
Защита Pillow от «бомб» распаковки тоже остаётся включённой. Перед docparse картинка
уменьшается до 2576 px по длинной стороне: docparse всё равно рисует страницу в 1288 px.

`inspect` вызывается в потоке: картинка до 10 МБ распаковывается заметное время. PDFium не
допускает одновременных вызовов из разных потоков, поэтому PDF открывается под блокировкой.
"""

import io
import threading
from dataclasses import dataclass

import pypdfium2 as pdfium
from PIL import Image, ImageOps

PDF = "application/pdf"
JPEG = "image/jpeg"
PNG = "image/png"
MAX_PAGES = 6
_SIGNATURES = ((b"%PDF-", PDF), (b"\x89PNG\r\n\x1a\n", PNG), (b"\xff\xd8\xff", JPEG))
_PILLOW_FORMATS = {JPEG: "JPEG", PNG: "PNG"}
_PDFIUM = threading.Lock()
MAX_PIXELS = 40_000_000
# Вдвое больше стороны, в которую docparse рисует страницу (`LONGEST_SIDE`): с запасом на обрезку.
MAX_SIDE = 2576


class DocumentFileError(Exception):
    """Файл не принят. `code` — код ошибки для ответа API."""

    code = "unreadable_document"


class UnsupportedDocumentError(DocumentFileError):
    code = "unsupported_document_type"


class TooManyPagesError(DocumentFileError):
    code = "too_many_pages"


class UnreadableDocumentError(DocumentFileError):
    code = "unreadable_document"


@dataclass(frozen=True)
class DocumentFile:
    """Тип файла по сигнатуре и число страниц: у картинки одна."""

    content_type: str
    pages: int


def inspect(data: bytes) -> DocumentFile:
    """Тип и число страниц файла.

    Raises:
        UnsupportedDocumentError: Не PDF, не JPEG и не PNG.
        TooManyPagesError: В PDF больше `MAX_PAGES` страниц.
        UnreadableDocumentError: Файл не открывается.
    """
    content_type = next((t for sign, t in _SIGNATURES if data.startswith(sign)), None)
    if content_type is None:
        raise UnsupportedDocumentError("unsupported document type")
    if content_type == PDF:
        pages = _pdf_pages(data)
        if pages > MAX_PAGES:
            raise TooManyPagesError(f"{pages} pages")
        return DocumentFile(content_type, pages)
    _open_image(data, content_type).close()
    return DocumentFile(content_type, 1)


def to_pdf(data: bytes, content_type: str) -> bytes:
    """PDF для docparse: PDF как есть, картинка — одна страница без текстового слоя.

    Картинка сначала поворачивается по метке ориентации EXIF: повёрнутую страницу
    docparse отклоняет.
    """
    if content_type == PDF:
        return data
    picture = _open_image(data, content_type)
    try:
        # Уменьшение сохраняет метку EXIF: поворот после него — на маленькой картинке.
        picture.thumbnail((MAX_SIDE, MAX_SIDE))
        upright = ImageOps.exif_transpose(picture)
        page = _flatten(upright)
        buffer = io.BytesIO()
        page.save(buffer, "PDF", resolution=72.0)
    finally:
        picture.close()
    return buffer.getvalue()


def _pdf_pages(data: bytes) -> int:
    with _PDFIUM:
        try:
            document = pdfium.PdfDocument(data)
        except pdfium.PdfiumError:
            raise UnreadableDocumentError("pdf does not open") from None
        try:
            return len(document)
        finally:
            document.close()


def _open_image(data: bytes, content_type: str) -> Image.Image:
    """Картинка, полностью прочитанная: обрезанный файл даёт ошибку здесь, а не в worker'е."""
    try:
        picture = Image.open(io.BytesIO(data), formats=[_PILLOW_FORMATS[content_type]])
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
        raise UnreadableDocumentError(type(exc).__name__) from None
    width, height = picture.size
    if width * height > MAX_PIXELS:
        picture.close()
        raise UnreadableDocumentError("image is too large")
    try:
        picture.load()
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
        picture.close()
        raise UnreadableDocumentError(type(exc).__name__) from None
    return picture


def _flatten(picture: Image.Image) -> Image.Image:
    """RGB или оттенки серого: прозрачный фон PNG становится белым, а не чёрным."""
    if picture.mode in {"RGB", "L"}:
        return picture
    if picture.mode in {"RGBA", "LA", "PA"} or "transparency" in picture.info:
        rgba = picture.convert("RGBA")
        white = Image.new("RGBA", rgba.size, "white")
        return Image.alpha_composite(white, rgba).convert("RGB")
    return picture.convert("RGB")
