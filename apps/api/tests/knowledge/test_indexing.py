"""Индексация источников манифеста (спека #27, раздел 5).

Модели и сеть не вызываются: API рубрикатора и docparse — подделки, эмбеддинги — «мешок
слов». Разбор настоящий, на синтетических документах.
"""

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from clin_compass_knowledge.schemas.source import Source
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core.ai.docparse import ParsedPage, ParsedPdf
from clin_compass.modules.knowledge import service
from clin_compass.modules.knowledge.indexing import Indexer, SourceReport
from clin_compass.modules.knowledge.models import Document, Fragment
from tests.knowledge.helpers import WordsEmbeddingModel, words_embedder

GRADE = (
    "<p><strong>Уровень убедительности рекомендаций – A "
    "(уровень достоверности доказательств – 1)</strong></p>"
)
TREATMENT = (
    "<p>Общие сведения о лечении бессонницы.</p>"
    "<ul><li><p>Пациентам с бессонницей рекомендуется соблюдать гигиену сна.</p></li></ul>" + GRADE
)
LABEL_PAGE = """ОБЩАЯ ХАРАКТЕРИСТИКА ЛЕКАРСТВЕННОГО ПРЕПАРАТА

4. КЛИНИЧЕСКИЕ ДАННЫЕ
4.1. Показания к применению
Препарат показан взрослым для лечения синтетического расстройства средней степени тяжести.

4.2. Режим дозирования и способ применения
Внутрь по 1 таблетке 2 раза в сутки независимо от приёма пищи, курсом не менее 8 недель.

4.3. Противопоказания
Гиперчувствительность к синтетину. Беременность и период грудного вскармливания.
"""


def _source(
    source_id: str, kind: str, *, version: str = "900_1", file: str | None = None
) -> Source:
    return Source.model_validate(
        {
            "id": source_id,
            "title": f"Документ {source_id}",
            "kind": kind,
            "publisher": "Издатель",
            "url": f"https://example.org/{source_id}",
            "version": version,
            "date": date(2026, 1, 1),
            "license": "синтетика для тестов",
            "file": file,
            "supplementary": False,
            # Коды МКБ-10 обязательны у КР и запрещены у других видов (спека #107).
            "icd10": ["F41.1"] if kind == "clinical_guideline" else [],
        }
    )


def _guideline(version: str = "900_1", treatment: str = TREATMENT) -> dict[str, Any]:
    return {
        "id": version,
        "obj": {
            "sections": [
                {
                    "id": "doc_title",
                    "title": "Титульный лист",
                    "content": "<p>Не индексируется.</p>",
                },
                {"id": "doc_3", "title": "3. Лечение", "content": treatment},
            ]
        },
    }


class FakeGuidelines:
    """API рубрикатора: ответ — JSON по версии; запоминает запрошенные версии."""

    def __init__(self, documents: dict[str, dict[str, Any]]) -> None:
        self.documents = documents
        self.requests: list[str] = []

    async def fetch(self, version: str) -> bytes:
        self.requests.append(version)
        return json.dumps(self.documents[version], ensure_ascii=False).encode()


class FakeDocparse:
    """docparse: страницы с текстом модели и текстовым слоем."""

    def __init__(self, *pages: tuple[str, str]) -> None:
        self.pages = tuple(ParsedPage(n, text, layer) for n, (text, layer) in enumerate(pages, 1))
        self.calls: list[str] = []

    async def parse_pdf(self, pdf: bytes, *, task: str) -> ParsedPdf:
        self.calls.append(task)
        return ParsedPdf(pages=self.pages)


def _indexer(
    sessionmaker: async_sessionmaker[AsyncSession],
    guidelines: FakeGuidelines,
    *,
    docparse: FakeDocparse | None = None,
    root: Path = Path("/nonexistent"),
) -> tuple[Indexer, WordsEmbeddingModel]:
    embedder, model = words_embedder()
    indexer = Indexer(sessionmaker, embedder, guidelines=guidelines, docparse=docparse, root=root)
    return indexer, model


async def _documents(maker: async_sessionmaker[AsyncSession]) -> list[tuple[str, str, str]]:
    async with maker() as session:
        rows = await session.execute(
            select(Document.source_id, Document.version, Document.status).order_by(
                Document.created_at
            )
        )
        return [(r.source_id, r.version, r.status) for r in rows]


async def _fragment_count(maker: async_sessionmaker[AsyncSession]) -> int:
    async with maker() as session:
        return int(await session.scalar(select(func.count()).select_from(Fragment)) or 0)


async def test_guideline_is_indexed_with_keys_levels_and_embeddings(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    guidelines = FakeGuidelines({"900_1": _guideline()})
    indexer, model = _indexer(sessionmaker, guidelines)

    [report] = await indexer.index([_source("kr-test", "clinical_guideline")])

    assert report == SourceReport("kr-test", "indexed", fragments=2)
    assert report.line() == "kr-test: проиндексирован, фрагментов: 2"
    async with sessionmaker() as session:
        document = await session.scalar(select(Document))
        assert document is not None
        raw = json.dumps(_guideline(), ensure_ascii=False).encode()
        assert document.content_hash == hashlib.sha256(raw).hexdigest()
        assert (document.status, document.kind, document.title) == (
            "active",
            "clinical_guideline",
            "Документ kr-test",
        )
        fragments = await service.read_section(session, "kr-test", "doc_3")
    assert [f.key for f in fragments] == ["kr-test/doc_3/1", "kr-test/doc_3/2"]
    assert [(f.kind, f.uur, f.udd) for f in fragments] == [
        ("text", None, None),
        ("recommendation", "A", 1),
    ]
    [(input_type, texts)] = model.calls
    assert input_type == "document"
    assert texts[0] == f"Документ kr-test. {fragments[0].heading_path}\n{fragments[0].text}"


async def test_rerun_without_changes_writes_nothing(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    indexer, model = _indexer(sessionmaker, FakeGuidelines({"900_1": _guideline()}))
    source = _source("kr-test", "clinical_guideline")
    await indexer.index([source])

    [report] = await indexer.index([source])

    assert report == SourceReport("kr-test", "unchanged")
    assert report.line() == "kr-test: без изменений"
    assert len(model.calls) == 1
    assert await _documents(sessionmaker) == [("kr-test", "900_1", "active")]


async def test_changed_source_supersedes_old_version_and_keeps_its_fragments(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    guidelines = FakeGuidelines({"900_1": _guideline()})
    indexer, _ = _indexer(sessionmaker, guidelines)
    source = _source("kr-test", "clinical_guideline")
    await indexer.index([source])
    async with sessionmaker() as session:
        old = (await service.read_section(session, "kr-test", "doc_3"))[0]
    item = "<ul><li><p>Пациентам с бессонницей рекомендуется дневник сна.</p></li></ul>"
    guidelines.documents["900_1"] = _guideline(treatment=f"<p>Новый текст о сне.</p>{item}{GRADE}")

    [report] = await indexer.index([source])

    assert report.status == "indexed"
    assert [s for _, _, s in await _documents(sessionmaker)] == ["superseded", "active"]
    assert await _fragment_count(sessionmaker) == 4
    async with sessionmaker() as session:
        # Старый фрагмент читается по-прежнему, его документ помечен прежней версией.
        superseded_ref = old.document.model_copy(update={"status": "superseded"})
        assert await service.get_fragment(session, old.id) == old.model_copy(
            update={"document": superseded_ref}
        )
        [new, _] = await service.read_section(session, "kr-test", "doc_3")
        superseded = await session.scalar(select(Document).where(Document.status == "superseded"))
    assert new.text == "Новый текст о сне."
    assert superseded is not None and superseded.superseded_at is not None


async def test_force_reindexes_unchanged_source(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    indexer, model = _indexer(sessionmaker, FakeGuidelines({"900_1": _guideline()}))
    source = _source("kr-test", "clinical_guideline")
    await indexer.index([source])

    [report] = await indexer.index([source], force=True)

    assert report.status == "indexed"
    assert len(model.calls) == 2
    assert [s for _, _, s in await _documents(sessionmaker)] == ["superseded", "active"]


async def test_guideline_id_must_match_manifest_version(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    indexer, _ = _indexer(sessionmaker, FakeGuidelines({"900_1": _guideline(version="900_2")}))

    [report] = await indexer.index([_source("kr-test", "clinical_guideline")])

    assert report.status == "error"
    assert "900_2" in report.detail and "900_1" in report.detail
    assert report.line().startswith("kr-test: ошибка: ")
    assert await _documents(sessionmaker) == []


async def test_parse_error_is_reported(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    indexer, _ = _indexer(sessionmaker, FakeGuidelines({"900_1": {"id": "900_1", "obj": {}}}))

    [report] = await indexer.index([_source("kr-test", "clinical_guideline")])

    assert report.status == "error"
    assert "obj.sections" in report.detail


@pytest.mark.parametrize(
    ("treatment", "match"),
    [
        pytest.param(
            "<p>Только текст о лечении, без пунктов.</p>", "ни одного пункта", id="нет пунктов"
        ),
        pytest.param("", "ни одного фрагмента", id="нет фрагментов"),
    ],
)
async def test_guideline_without_items_is_error_and_keeps_active_version(
    sessionmaker: async_sessionmaker[AsyncSession], treatment: str, match: str
) -> None:
    guidelines = FakeGuidelines({"900_1": _guideline()})
    indexer, _ = _indexer(sessionmaker, guidelines)
    source = _source("kr-test", "clinical_guideline")
    await indexer.index([source])
    guidelines.documents["900_1"] = _guideline(treatment=treatment)

    [report] = await indexer.index([source])

    assert report.status == "error"
    assert match in report.detail
    assert await _documents(sessionmaker) == [("kr-test", "900_1", "active")]


async def test_embeddings_are_computed_in_batches_of_8(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    many = "".join(f"<h2>Подраздел {n}</h2><p>Текст {n}.</p>" for n in range(19)) + TREATMENT
    indexer, model = _indexer(sessionmaker, FakeGuidelines({"900_1": _guideline(treatment=many)}))

    [report] = await indexer.index([_source("kr-test", "clinical_guideline")])

    assert report.fragments == 20
    assert [len(texts) for _, texts in model.calls] == [8, 8, 4]


async def test_label_without_file_is_error_and_other_sources_are_indexed(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    indexer, _ = _indexer(sessionmaker, FakeGuidelines({"900_1": _guideline()}))
    sources = [
        _source("other-ohlp", "drug_label"),
        _source("grls", "drug_registry", file=None),
        # Листок-вкладыш написан для пациента: база знаний врача берёт ОХЛП.
        _source("test-leaflet", "patient_leaflet", file="files/leaflet.pdf"),
        _source("kr-test", "clinical_guideline"),
    ]

    reports = await indexer.index(sources)

    assert [(r.source_id, r.status) for r in reports] == [
        ("other-ohlp", "error"),
        ("kr-test", "indexed"),
    ]


@pytest.fixture
def label_root(tmp_path: Path) -> Path:
    (tmp_path / "sources" / "files").mkdir(parents=True)
    (tmp_path / "sources" / "files" / "label.pdf").write_bytes(b"%PDF-synthetic")
    return tmp_path


async def test_label_pdf_is_parsed_checked_and_indexed(
    sessionmaker: async_sessionmaker[AsyncSession], label_root: Path
) -> None:
    docparse = FakeDocparse((LABEL_PAGE, LABEL_PAGE))
    indexer, _ = _indexer(sessionmaker, FakeGuidelines({}), docparse=docparse, root=label_root)
    source = _source("test-ohlp", "drug_label", version="0001", file="files/label.pdf")

    [report] = await indexer.index([source])
    [rerun] = await indexer.index([source])

    assert report.status == "indexed"
    assert rerun.status == "unchanged"
    assert docparse.calls == ["knowledge.parse"]
    async with sessionmaker() as session:
        [contraindications] = await service.read_section(session, "test-ohlp", "4.3")
        document = await session.scalar(select(Document))
    assert "Беременность" in contraindications.text
    assert document is not None
    assert document.content_hash == hashlib.sha256(b"%PDF-synthetic").hexdigest()


async def test_label_words_come_from_text_layer(
    sessionmaker: async_sessionmaker[AsyncSession], label_root: Path
) -> None:
    # Модель ошиблась в дозе, в текстовом слое PDF — верная.
    layer = LABEL_PAGE.replace("по 1 таблетке", "по 2 таблетки")
    docparse = FakeDocparse((LABEL_PAGE, layer))
    indexer, _ = _indexer(sessionmaker, FakeGuidelines({}), docparse=docparse, root=label_root)
    source = _source("test-ohlp", "drug_label", version="0001", file="files/label.pdf")

    [report] = await indexer.index([source])

    assert report.status == "indexed"
    async with sessionmaker() as session:
        [dosing] = await service.read_section(session, "test-ohlp", "4.2")
    assert "Внутрь по 2 таблетки 2 раза в сутки" in dosing.text


async def test_text_layer_mismatch_stops_label_and_names_page(
    sessionmaker: async_sessionmaker[AsyncSession], label_root: Path
) -> None:
    # Модель пропустила два раздела: порядок чтения разошёлся со слоем.
    model = LABEL_PAGE[LABEL_PAGE.index("4.3.") :]
    docparse = FakeDocparse((model, LABEL_PAGE))
    indexer, _ = _indexer(sessionmaker, FakeGuidelines({}), docparse=docparse, root=label_root)
    source = _source("test-ohlp", "drug_label", version="0001", file="files/label.pdf")

    [report] = await indexer.index([source])

    assert report.status == "error"
    assert "страница 1: не сведено" in report.detail
    assert "(нет) → " in report.detail
    assert await _documents(sessionmaker) == []


async def test_reordered_table_page_is_indexed_from_layer_with_note(
    sessionmaker: async_sessionmaker[AsyncSession], label_root: Path
) -> None:
    # Модель собрала частоты трёх строк в одну ячейку, слой читает таблицу по строкам.
    # Таблица — малая часть страницы, как в настоящих ОХЛП.
    page = LABEL_PAGE + " ".join(f"Предложение {n} о синтетине без изменений." for n in range(50))
    terms = [f"реакция{chr(0x430 + n)}" for n in range(12)]
    frequencies = ["часто", "нечасто", "редко"] * 4
    model = (
        page
        + "\n<table>"
        + "".join(
            "<tr><td>{}</td><td>{}</td></tr>".format(
                "<br>".join(frequencies[:3]), "<br>".join(terms[n : n + 3])
            )
            for n in range(0, len(terms), 3)
        )
        + "</table>\n"
    )
    layer = page + "\n" + "\n".join(f"{t} {f}" for t, f in zip(terms, frequencies, strict=True))
    docparse = FakeDocparse((model, layer))
    indexer, _ = _indexer(sessionmaker, FakeGuidelines({}), docparse=docparse, root=label_root)
    source = _source("test-ohlp", "drug_label", version="0001", file="files/label.pdf")

    [report] = await indexer.index([source])

    assert report.status == "indexed"
    assert report.notes == ("страница 1: текст из слоя PDF без разметки модели",)
    async with sessionmaker() as session:
        section = await service.read_section(session, "test-ohlp", "4.3")
    assert "реакцияа часто\nреакцияб нечасто" in "\n".join(f.text for f in section)


async def test_label_without_fragments_is_error(
    sessionmaker: async_sessionmaker[AsyncSession], label_root: Path
) -> None:
    page = "4.1. Показания к применению\n4.2. Режим дозирования\n4.3. Противопоказания\n"
    docparse = FakeDocparse((page, page))
    indexer, _ = _indexer(sessionmaker, FakeGuidelines({}), docparse=docparse, root=label_root)
    source = _source("test-ohlp", "drug_label", version="0001", file="files/label.pdf")

    [report] = await indexer.index([source])

    assert report.status == "error"
    assert "ни одного фрагмента" in report.detail
    assert await _documents(sessionmaker) == []


async def test_label_without_docparse_nodes_is_error(
    sessionmaker: async_sessionmaker[AsyncSession], label_root: Path
) -> None:
    indexer, _ = _indexer(sessionmaker, FakeGuidelines({}), docparse=None, root=label_root)
    source = _source("test-ohlp", "drug_label", version="0001", file="files/label.pdf")

    [report] = await indexer.index([source])

    assert report.status == "error"
    assert "docparse" in report.detail


async def test_page_without_text_layer_stops_label(
    sessionmaker: async_sessionmaker[AsyncSession], label_root: Path
) -> None:
    # Скан: в PDF нет текстового слоя, сводить не с чем. Сканы — задача #30.
    docparse = FakeDocparse((LABEL_PAGE, LABEL_PAGE), (LABEL_PAGE, ""))
    indexer, _ = _indexer(sessionmaker, FakeGuidelines({}), docparse=docparse, root=label_root)
    source = _source("test-ohlp", "drug_label", version="0001", file="files/label.pdf")

    [report] = await indexer.index([source])

    assert report.status == "error"
    assert "страница 2: нет текстового слоя" in report.detail
    assert await _documents(sessionmaker) == []
