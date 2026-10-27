"""Источник для инспектора: документ из манифеста и раздел по `locator` (спека #106, 3.1–3.5).

Индекс — небольшая копия разметки настоящих документов: у ОХЛП `section` — номер раздела,
у КР — ключ разметки, а номер подраздела есть только в `heading_path`. Справочник
источников собран из knowledge/ рабочей копии.
"""

from pathlib import Path
from uuid import uuid4

import clin_compass_knowledge
import pytest
from clin_compass_knowledge.grls import Registry, RegistryEntry
from clin_compass_knowledge.loader import Knowledge, load_knowledge
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.modules.knowledge import service
from clin_compass.modules.knowledge.models import Fragment
from clin_compass.modules.knowledge.schemas import SourceView
from tests.knowledge.helpers import Piece, add_document

KNOWLEDGE = Path(clin_compass_knowledge.__file__).resolve().parents[2]
OHLP = "aviandr-ohlp"
KR = "kr-depression-2024"
CLINICAL = "4. КЛИНИЧЕСКИЕ ДАННЫЕ"
TREATMENT = "3. Лечение"
FIRST_STAGE = f"{TREATMENT} > 3.1 Купирующая терапия ДЭ на первом этапе"
SEVERE = f"{FIRST_STAGE} > 3.1.3 Купирующая терапия ДЭ тяжёлой степени без психотических симптомов"


def ohlp_piece(section: str, title: str, text: str, chapter: str = CLINICAL) -> Piece:
    return Piece(
        text, section=section, section_title=title, heading_path=f"{chapter} > {section}. {title}"
    )


OHLP_PIECES = [
    ohlp_piece("4.2", "Режим дозирования и способ применения", "Пациенты с почечной…"),
    ohlp_piece("4.3", "Противопоказания", "Гиперчувствительность."),
    ohlp_piece("4.3", "Противопоказания", "Беременность."),
    ohlp_piece("5.1", "Фармакодинамические свойства", "Механизм.", "5. ФАРМАКОЛОГИЧЕСКИЕ СВОЙСТВА"),
]
KR_PIECES = [
    Piece(
        "Иные исследования.",
        section="doc_diag_2_5",
        section_title="2.5 Иные исследования",
        heading_path="2.5 Иные исследования",
    ),
    Piece("Общие принципы.", section="doc_3", section_title=TREATMENT, heading_path=TREATMENT),
    Piece("Первый этап.", section="doc_3", section_title=TREATMENT, heading_path=FIRST_STAGE),
    Piece("Тяжёлая без психоза.", section="doc_3", section_title=TREATMENT, heading_path=SEVERE),
    Piece("Стационар.", section="doc_3", section_title=TREATMENT, heading_path=SEVERE),
    Piece(
        "Второй этап.",
        section="doc_3",
        section_title=TREATMENT,
        heading_path=f"{TREATMENT} > 3.2 Купирующая терапия ДЭ на втором этапе",
    ),
]


@pytest.fixture(scope="module")
def knowledge() -> Knowledge:
    return load_knowledge(KNOWLEDGE)


@pytest.fixture(scope="module")
def catalog(knowledge: Knowledge) -> service.SourceCatalog:
    return service.SourceCatalog.from_knowledge(knowledge)


@pytest.fixture
async def index(sessionmaker: async_sessionmaker[AsyncSession]) -> dict[str, list[Fragment]]:
    """ОХЛП и КР в действующей версии и прежняя версия КР с другим текстом раздела 3.1.3."""
    async with sessionmaker() as session:
        _, ohlp = await add_document(
            session, OHLP, OHLP_PIECES, kind="drug_label", title="ОХЛП", version="0010"
        )
        _, old = await add_document(
            session,
            KR,
            [Piece("Прежний текст.", section_title=TREATMENT, heading_path=SEVERE)],
            status="superseded",
            version="301_2",
        )
        _, kr = await add_document(session, KR, KR_PIECES, version="301_3")
        await session.commit()
    return {OHLP: ohlp, KR: kr, "old": old}


async def view(
    sessionmaker: async_sessionmaker[AsyncSession],
    catalog: service.SourceCatalog,
    source_id: str,
    locator: str,
) -> SourceView:
    async with sessionmaker() as session:
        result = await service.view_source(session, catalog, source_id, locator=locator)
    assert result is not None
    return result


def texts(result: SourceView) -> list[list[str]]:
    return [[f.text for f in section.fragments] for section in result.sections]


async def test_label_section_is_found_by_number(
    sessionmaker: async_sessionmaker[AsyncSession],
    catalog: service.SourceCatalog,
    knowledge: Knowledge,
    index: dict[str, list[Fragment]],
) -> None:
    # drugs/aviandr.yaml, противопоказания
    result = await view(sessionmaker, catalog, OHLP, "4.3")

    assert result.found
    assert result.locator == "4.3"
    [section] = result.sections
    assert (section.number, section.title) == ("4.3", "Противопоказания")
    assert texts(result) == [["Гиперчувствительность.", "Беременность."]]
    manifest = knowledge.sources[OHLP]
    assert result.source.title == manifest.title
    assert (result.source.kind, result.source.version) == ("drug_label", manifest.version)
    assert (result.source.date, result.source.url) == (manifest.date, str(manifest.url))
    assert (result.source.publisher, result.source.supplementary) == (manifest.publisher, False)


async def test_label_has_registration_from_reviewed_drug(
    sessionmaker: async_sessionmaker[AsyncSession],
    catalog: service.SourceCatalog,
    knowledge: Knowledge,
    index: dict[str, list[Fragment]],
) -> None:
    result = await view(sessionmaker, catalog, OHLP, "4.3")

    drug = knowledge.drugs["aviandr"]
    snapshot = knowledge.sources[drug.registration.source.source]
    assert result.registry is not None
    assert result.registry.reg_number == drug.registration.number == "ЛП-№(002445)-(РГ-RU)"
    assert result.registry.status == "active"
    assert result.registry.snapshot_date == snapshot.date


def test_catalog_takes_registry_snapshots_from_caller(knowledge: Knowledge) -> None:
    # main.py передаёт снимки, уже прочитанные справочником препаратов: файл ГРЛС
    # при старте разбирается один раз. Статус берётся из переданного снимка.
    drug = knowledge.drugs["aviandr"]
    entry = RegistryEntry(drug.registration.number, "АВИАНДР®", "Маритупирдин", "suspended")

    catalog = service.SourceCatalog.from_knowledge(
        knowledge, registries={drug.registration.source.source: Registry([entry])}
    )

    assert catalog.registrations[OHLP].status == "suspended"


async def test_label_without_reviewed_drug_has_no_registration(
    sessionmaker: async_sessionmaker[AsyncSession], catalog: service.SourceCatalog
) -> None:
    # Карточки Ципралекса в knowledge/drugs нет.
    result = await view(sessionmaker, catalog, "cipralex-ohlp", "4.3")

    assert result.registry is None
    assert not result.found


async def test_guideline_subsection_is_found_in_heading_path(
    sessionmaker: async_sessionmaker[AsyncSession],
    catalog: service.SourceCatalog,
    index: dict[str, list[Fragment]],
) -> None:
    # rules/hamd17-suicidal-intent.yaml: раздел 3.1.3 лежит внутри `doc_3`.
    locator = "раздел 3.1.3: ДЭ с высоким суицидальным риском — лечение в стационаре, УУР B (УДД 1)"

    result = await view(sessionmaker, catalog, KR, locator)

    assert result.found
    [section] = result.sections
    assert section.number == "3.1.3"
    assert section.title == "Купирующая терапия ДЭ тяжёлой степени без психотических симптомов"
    assert texts(result) == [["Тяжёлая без психоза.", "Стационар."]]
    assert {f.document.status for f in section.fragments} == {"active"}
    assert result.registry is None


async def test_section_includes_its_subsections(
    sessionmaker: async_sessionmaker[AsyncSession],
    catalog: service.SourceCatalog,
    index: dict[str, list[Fragment]],
) -> None:
    result = await view(sessionmaker, catalog, KR, "раздел 3.1")

    [section] = result.sections
    assert section.title == "Купирующая терапия ДЭ на первом этапе"
    assert texts(result) == [["Первый этап.", "Тяжёлая без психоза.", "Стационар."]]


async def test_label_chapter_includes_numbered_sections(
    sessionmaker: async_sessionmaker[AsyncSession],
    catalog: service.SourceCatalog,
    index: dict[str, list[Fragment]],
) -> None:
    # drugs/aviandr.yaml, листок-вкладыш: «раздел 4, …»; у ОХЛП раздел 4 — это 4.1–4.9.
    result = await view(sessionmaker, catalog, OHLP, "раздел 4, «Способ применения»")

    [section] = result.sections
    assert (section.number, section.title) == ("4", "КЛИНИЧЕСКИЕ ДАННЫЕ")
    assert texts(result) == [["Пациенты с почечной…", "Гиперчувствительность.", "Беременность."]]


async def test_guideline_section_by_key_number(
    sessionmaker: async_sessionmaker[AsyncSession],
    catalog: service.SourceCatalog,
    index: dict[str, list[Fragment]],
) -> None:
    # rules/phq9-suicidal-thoughts.yaml
    locator = "раздел 2.5: обследование для выявления факторов риска суицида — всем пациентам"

    result = await view(sessionmaker, catalog, KR, locator)

    [section] = result.sections
    assert (section.number, section.title) == ("2.5", "Иные исследования")
    assert texts(result) == [["Иные исследования."]]


async def test_two_sections_follow_locator_order(
    sessionmaker: async_sessionmaker[AsyncSession],
    catalog: service.SourceCatalog,
    index: dict[str, list[Fragment]],
) -> None:
    # drugs/aviandr.yaml, почечная недостаточность
    locator = "4.3; 4.2, «Пациенты с почечной недостаточностью»"

    result = await view(sessionmaker, catalog, OHLP, locator)

    assert [s.number for s in result.sections] == ["4.3", "4.2"]
    assert texts(result) == [["Гиперчувствительность.", "Беременность."], ["Пациенты с почечной…"]]


@pytest.mark.parametrize(
    ("source_id", "locator"),
    [
        # rules/routing-depression-severe.yaml: приложения Г в индексе нет.
        (KR, "прил. Г1, ключ: HAMD-17 19–22 — тяжёлая степень"),
        # Раздела 7 в индексе-фикстуре нет.
        (OHLP, "7.1"),
        # rules/routing-anxiety-probable.yaml: документов валидации шкал в индексе нет.
        ("zolotareva-gad7-2023", "с. 39: GAD-7 ≥ 10 — вероятный диагноз ГТР"),
        # drugs/aviandr.yaml: листок-вкладыш база знаний врача не индексирует.
        ("aviandr-leaflet-2026", "раздел 4, «Немедленно сообщите лечащему врачу»"),
    ],
)
async def test_missing_text_gives_document_without_sections(
    sessionmaker: async_sessionmaker[AsyncSession],
    catalog: service.SourceCatalog,
    knowledge: Knowledge,
    index: dict[str, list[Fragment]],
    source_id: str,
    locator: str,
) -> None:
    result = await view(sessionmaker, catalog, source_id, locator)

    assert not result.found
    assert result.sections == []
    assert result.locator == locator
    assert result.source.id == source_id
    assert result.source.kind == knowledge.sources[source_id].kind


async def test_fragment_of_previous_version_is_shown_with_its_status(
    sessionmaker: async_sessionmaker[AsyncSession],
    catalog: service.SourceCatalog,
    index: dict[str, list[Fragment]],
) -> None:
    [old] = index["old"]

    async with sessionmaker() as session:
        result = await service.view_source(session, catalog, KR, fragment_id=old.id)

    assert result is not None
    assert (result.found, result.locator) == (True, None)
    [section] = result.sections
    assert section.number == "3.1.3"
    assert section.title == "Купирующая терапия ДЭ тяжёлой степени без психотических симптомов"
    [fragment] = section.fragments
    assert (fragment.text, fragment.document.status) == ("Прежний текст.", "superseded")
    assert fragment.document.version == "301_2"


async def test_fragment_without_numbered_heading_has_no_number(
    sessionmaker: async_sessionmaker[AsyncSession], catalog: service.SourceCatalog
) -> None:
    async with sessionmaker() as session:
        _, [terms] = await add_document(
            session,
            KR,
            [
                Piece(
                    "Термин.",
                    section="doc_terms",
                    section_title="Термины и определения",
                    heading_path="Термины и определения",
                )
            ],
        )
        await session.commit()
        result = await service.view_source(session, catalog, KR, fragment_id=terms.id)

    assert result is not None
    [section] = result.sections
    assert (section.number, section.title) == (None, "Термины и определения")


async def test_fragment_of_other_source_is_not_found(
    sessionmaker: async_sessionmaker[AsyncSession],
    catalog: service.SourceCatalog,
    index: dict[str, list[Fragment]],
) -> None:
    async with sessionmaker() as session:
        other = await service.view_source(session, catalog, OHLP, fragment_id=index[KR][0].id)
        missing = await service.view_source(session, catalog, KR, fragment_id=uuid4())

    assert (other, missing) == (None, None)


async def test_unknown_source_is_not_found(
    sessionmaker: async_sessionmaker[AsyncSession], catalog: service.SourceCatalog
) -> None:
    async with sessionmaker() as session:
        result = await service.view_source(session, catalog, "no-such-source", locator="4.3")

    assert result is None
