"""Прогон оценки без узлов: имя результата, время страниц, пределы worker'а (спека #30, 7).

uv run --package clin-compass-eval pytest eval/extraction
"""

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from clin_compass.core.ai.docparse import ParsedPage, ParsedPdf
from clin_compass.core.ai.runs import RunRecord
from clin_compass.modules.ingestion.analysis import Extraction

from run import PageTimes, ocr_case, result_path

# Как в eval/rag: асинхронные тесты идут через плагин anyio.
pytestmark = pytest.mark.anyio


class FakeDocparse:
    def __init__(self, *pages: ParsedPage) -> None:
        self._pages = pages

    async def parse_pdf(self, pdf: bytes, *, task: str) -> ParsedPdf:
        return ParsedPdf(pages=self._pages)


class FakeExtractor:
    def __init__(self) -> None:
        self.texts: list[tuple[str, bool]] = []

    async def extract(self, text: str, *, ocr: bool) -> Extraction:
        self.texts.append((text, ocr))
        return Extraction(None, (), (), (), 0, uuid4())


def _run(role: str, status: str, latency_ms: int) -> RunRecord:
    return RunRecord(
        started_at=datetime.now(UTC),
        role=role,  # type: ignore[arg-type]
        task="eval.extraction",
        model="m",
        node=None,
        prompt_version=None,
        params={},
        input_hash="h",
        status=status,  # type: ignore[arg-type]
        error=None,
        latency_ms=latency_ms,
        input_tokens=None,
        output_tokens=None,
        audio_seconds=None,
    )


def test_result_name_has_time_so_runs_of_one_day_do_not_overwrite(tmp_path: Path) -> None:
    now = datetime(2026, 10, 22, 9, 5, 7, tzinfo=UTC)

    path = result_path(tmp_path, "qwen", "extract-12345678", now)

    assert path == tmp_path / "2026-10-22-090507-qwen-extract-12345678.json"


async def test_page_times_keep_successful_docparse_pages_only() -> None:
    times = PageTimes()

    for run in (_run("docparse", "ok", 1200), _run("docparse", "error", 90), _run("llm", "ok", 5)):
        await times.record(run)

    assert times.seconds == [1.2]


async def test_ocr_case_applies_worker_text_limits() -> None:
    extractor = FakeExtractor()
    long_page = ParsedPage(number=1, text="Текст. " * 2200, layer_text="")

    found, problem, sources = await ocr_case(
        extractor,  # type: ignore[arg-type]
        FakeDocparse(long_page),  # type: ignore[arg-type]
        b"%PDF-",
        "application/pdf",
    )

    assert (found, problem, sources, extractor.texts) == (None, "text_too_long", ["ocr"], [])


async def test_ocr_case_extracts_worker_text() -> None:
    extractor = FakeExtractor()
    table = "<table><tr><td>3</td><td>Суицидальные намерения</td><td>1</td></tr></table>"

    found, problem, _ = await ocr_case(
        extractor,  # type: ignore[arg-type]
        FakeDocparse(ParsedPage(number=1, text=table, layer_text="")),  # type: ignore[arg-type]
        b"%PDF-",
        "application/pdf",
    )

    assert problem is None and found is not None
    assert extractor.texts == [("3 | Суицидальные намерения | 1", True)]
