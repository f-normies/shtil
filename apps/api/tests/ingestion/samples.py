"""Файлы для тестов ingestion: PDF из пустых страниц, картинки с меткой поворота EXIF."""

import io

import pypdfium2 as pdfium
from PIL import Image

# Метка ориентации EXIF: 6 — снимок нужно повернуть на 90° по часовой стрелке.
ORIENTATION = 0x0112


def pdf(pages: int = 1) -> bytes:
    """PDF из `pages` пустых страниц A4 без текстового слоя."""
    document = pdfium.PdfDocument.new()
    try:
        for _ in range(pages):
            document.new_page(595, 842)
        buffer = io.BytesIO()
        document.save(buffer)
    finally:
        document.close()
    return buffer.getvalue()


def image(fmt: str, size: tuple[int, int] = (40, 20), orientation: int | None = None) -> bytes:
    """Белая картинка `fmt` (JPEG или PNG); у JPEG может быть метка поворота EXIF."""
    picture = Image.new("RGB", size, "white")
    buffer = io.BytesIO()
    if orientation is None:
        picture.save(buffer, fmt)
    else:
        exif = picture.getexif()
        exif[ORIENTATION] = orientation
        picture.save(buffer, fmt, exif=exif)
    return buffer.getvalue()


def page_sizes(data: bytes) -> list[tuple[float, float]]:
    """Размеры страниц PDF в пунктах."""
    document = pdfium.PdfDocument(data)
    try:
        return [document[i].get_size() for i in range(len(document))]
    finally:
        document.close()


def layer_texts(data: bytes) -> list[str]:
    """Текстовый слой каждой страницы PDF."""
    document = pdfium.PdfDocument(data)
    texts = []
    try:
        for i in range(len(document)):
            page = document[i]
            textpage = page.get_textpage()
            texts.append(textpage.get_text_bounded())
            textpage.close()
            page.close()
    finally:
        document.close()
    return texts
