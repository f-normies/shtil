"""Задача `ingestion.parse`: текст документа, ошибки и повторы (спека #30, 3.3 и 3.6)."""

from datetime import timedelta

import pytest
from clin_compass_knowledge.grls import Registry
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core.ai.docparse import DocparsePageError, ParsedPage
from clin_compass.core.ai.nodes import NodesUnavailableError
from clin_compass.core.queue import RetryRequested
from clin_compass.core.storage import StoredObject
from tests.freetext.conftest import Care
from tests.ingestion.helpers import BrokenStorage, MemoryStorage, jobs
from tests.ingestion.jobs import FakeParser, add_document, load, make_jobs, page
from tests.ingestion.samples import image, page_sizes

MODEL = "# Выписной эпикриз\n\nДиагноз: F33.1. Принимал флуоксамин 100 мг на ночь."
LAYER = "Выписной эпикриз\r\nДиагноз: F33.1. Принимал флувоксамин 100 мг на ночь."
RECONCILED = "# Выписной эпикриз\n\nДиагноз: F33.1. Принимал флувоксамин 100 мг на ночь."
# Модель потеряла абзац: длинный несведённый участок, разметке модели верить нельзя.
LOST = "Анамнез без подробностей."
PLAIN = (
    "Анамнез: первый эпизод в 2019 году после смерти матери, лечился паро\x02ксетином,\r\n"
    "ремиссия. Второй эпизод в 2024 году, повторный курс без эффекта, отменён."
)


async def test_page_with_layer_is_reconciled(
    sessionmaker: async_sessionmaker[AsyncSession], care: Care, registry: Registry
) -> None:
    store = MemoryStorage()
    parser = FakeParser((page(1, MODEL, LAYER),))
    worker, _ = make_jobs(sessionmaker, registry, parser=parser, storage=store)
    payload = await add_document(sessionmaker, care, store)

    await worker.parse(payload)

    document = await load(sessionmaker, payload)
    assert document.text == RECONCILED
    assert (document.status, document.ocr) == ("extracting", False)
    assert document.page_sources == [
        {"page": 1, "source": "layer_markup", "start": 0, "end": len(RECONCILED)}
    ]
    assert document.parsed_at is not None
    assert parser.calls == [(store.objects["documents/0"][0], "ingestion.parse")]
    assert (await jobs(sessionmaker))[-1] == ("ingestion.extract", payload)


async def test_page_that_does_not_reconcile_is_taken_from_layer(
    sessionmaker: async_sessionmaker[AsyncSession], care: Care, registry: Registry
) -> None:
    store = MemoryStorage()
    worker, _ = make_jobs(
        sessionmaker, registry, parser=FakeParser((page(1, LOST, PLAIN),)), storage=store
    )
    payload = await add_document(sessionmaker, care, store)

    await worker.parse(payload)

    document = await load(sessionmaker, payload)
    assert document.text == PLAIN.replace("\x02", "").replace("\r\n", "\n")
    assert [p["source"] for p in document.page_sources] == ["layer"]
    assert document.ocr is False


async def test_page_without_layer_is_ocr(
    sessionmaker: async_sessionmaker[AsyncSession], care: Care, registry: Registry
) -> None:
    store = MemoryStorage()
    parser = FakeParser((page(1, MODEL, LAYER), page(2, "Сертралин 50 мг утром.")))
    worker, _ = make_jobs(sessionmaker, registry, parser=parser, storage=store)
    payload = await add_document(sessionmaker, care, store)

    await worker.parse(payload)

    document = await load(sessionmaker, payload)
    assert document.text == f"{RECONCILED}\n\nСертралин 50 мг утром."
    second = len(RECONCILED) + 2
    assert document.page_sources[1] == {
        "page": 2,
        "source": "ocr",
        "start": second,
        "end": second + len("Сертралин 50 мг утром."),
    }
    assert document.ocr is True


async def test_image_goes_to_docparse_as_one_page_pdf(
    sessionmaker: async_sessionmaker[AsyncSession], care: Care, registry: Registry
) -> None:
    store = MemoryStorage()
    parser = FakeParser((page(1, "Сертралин 50 мг утром."),))
    worker, _ = make_jobs(sessionmaker, registry, parser=parser, storage=store)
    payload = await add_document(
        sessionmaker, care, store, data=image("PNG", (30, 50)), content_type="image/png"
    )

    await worker.parse(payload)

    [(sent, _)] = parser.calls
    assert page_sizes(sent) == [(30.0, 50.0)]
    assert (await load(sessionmaker, payload)).ocr is True


async def test_unreadable_page_fails_at_once_with_its_number(
    sessionmaker: async_sessionmaker[AsyncSession], care: Care, registry: Registry
) -> None:
    store = MemoryStorage()
    parser = FakeParser(DocparsePageError(2, "модель считает страницу повёрнутой"))
    worker, _ = make_jobs(sessionmaker, registry, parser=parser, storage=store)
    payload = await add_document(sessionmaker, care, store)

    await worker.parse(payload)

    document = await load(sessionmaker, payload)
    assert (document.status, document.error_code, document.error_page) == (
        "failed",
        "page_unreadable",
        2,
    )
    assert [j for j, _ in await jobs(sessionmaker)] == []


@pytest.mark.parametrize(
    ("pages", "code"),
    [
        ((page(1, "", ""), page(2, "  \n", "")), "empty_text"),
        ((page(1, "Текст. " * 2200),), "text_too_long"),
    ],
)
async def test_empty_or_too_long_text_fails(
    sessionmaker: async_sessionmaker[AsyncSession],
    care: Care,
    registry: Registry,
    pages: tuple[ParsedPage, ...],
    code: str,
) -> None:
    store = MemoryStorage()
    worker, _ = make_jobs(sessionmaker, registry, parser=FakeParser(pages), storage=store)
    payload = await add_document(sessionmaker, care, store)

    await worker.parse(payload)

    document = await load(sessionmaker, payload)
    assert (document.status, document.error_code, document.text) == ("failed", code, None)


@pytest.mark.parametrize(
    "setup",
    ["node", "storage_down", "storage_missing"],
)
async def test_unavailable_node_or_storage_waits_without_counting(
    sessionmaker: async_sessionmaker[AsyncSession], care: Care, registry: Registry, setup: str
) -> None:
    store = MemoryStorage()
    parser = FakeParser(NodesUnavailableError("docparse"))
    payload = await add_document(sessionmaker, care, store)
    storage = {"node": store, "storage_down": BrokenStorage(), "storage_missing": None}[setup]
    worker, _ = make_jobs(sessionmaker, registry, parser=parser, storage=storage)

    with pytest.raises(RetryRequested) as retry:
        await worker.parse(payload)

    assert retry.value.delay == timedelta(seconds=30)
    document = await load(sessionmaker, payload)
    assert (document.status, document.parse_failures) == ("parsing", 0)


async def test_five_errors_mark_parse_failed(
    sessionmaker: async_sessionmaker[AsyncSession], care: Care, registry: Registry
) -> None:
    store = MemoryStorage()
    worker, _ = make_jobs(
        sessionmaker, registry, parser=FakeParser(RuntimeError("boom")), storage=store
    )
    payload = await add_document(sessionmaker, care, store)
    delays = []
    for _ in range(4):
        with pytest.raises(RetryRequested) as retry:
            await worker.parse(payload)
        delays.append(retry.value.delay.total_seconds())

    await worker.parse(payload)

    assert delays == [2, 4, 8, 16]
    document = await load(sessionmaker, payload)
    assert (document.status, document.error_code, document.parse_failures) == (
        "failed",
        "parse_failed",
        5,
    )


async def test_repeated_job_after_result_does_nothing(
    sessionmaker: async_sessionmaker[AsyncSession], care: Care, registry: Registry
) -> None:
    store = MemoryStorage()
    parser = FakeParser((page(1, MODEL, LAYER),))
    worker, _ = make_jobs(sessionmaker, registry, parser=parser, storage=store)
    payload = await add_document(sessionmaker, care, store)

    await worker.parse(payload)
    await worker.parse(payload)

    assert len(parser.calls) == 1
    assert [j for j, _ in await jobs(sessionmaker)] == ["ingestion.extract"]


async def test_reconciled_text_has_plain_signs_for_quotes(
    sessionmaker: async_sessionmaker[AsyncSession], care: Care, registry: Registry
) -> None:
    # Сведённый текст — разметка с экранированными «<» и «&»; цитата модели их не экранирует.
    store = MemoryStorage()
    model = "Анализы: СКФ < 60 мл/мин, АЛТ & АСТ в норме."
    parser = FakeParser((page(1, model, model),))
    worker, _ = make_jobs(sessionmaker, registry, parser=parser, storage=store)
    payload = await add_document(sessionmaker, care, store)

    await worker.parse(payload)

    assert (await load(sessionmaker, payload)).text == model


class _BrokenStream(MemoryStorage):
    """S3 отдаёт объект, но поток обрывается при чтении."""

    def get(self, key: str, *, byte_range: str | None = None) -> StoredObject:
        stored = super().get(key, byte_range=byte_range)

        def broken(size: int = -1) -> bytes:
            raise ConnectionResetError("stream reset")

        stored.body.read = broken  # type: ignore[method-assign]
        return stored


async def test_broken_storage_stream_waits_without_counting(
    sessionmaker: async_sessionmaker[AsyncSession], care: Care, registry: Registry
) -> None:
    store = _BrokenStream()
    worker, _ = make_jobs(
        sessionmaker, registry, parser=FakeParser((page(1, MODEL, LAYER),)), storage=store
    )
    payload = await add_document(sessionmaker, care, store)

    with pytest.raises(RetryRequested) as retry:
        await worker.parse(payload)

    assert retry.value.delay == timedelta(seconds=30)
    assert (await load(sessionmaker, payload)).parse_failures == 0
