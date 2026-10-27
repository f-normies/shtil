"""Справочник источников модуля knowledge и вид источника для инспектора (спека #106, 3.3–3.5).

Справочник собирается при старте api из того же снимка knowledge/, что правила и препараты:
манифест, проверенные карточки препаратов и снимки ГРЛС. Справочник `therapy` не нужен:
у модуля свой.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

import structlog
from clin_compass_knowledge.grls import Registry, load_registry
from clin_compass_knowledge.loader import Knowledge
from clin_compass_knowledge.schemas.drug import Drug
from clin_compass_knowledge.schemas.source import Source
from sqlalchemy.ext.asyncio import AsyncSession

from clin_compass.modules.knowledge.schemas import RegistryInfo, SourceInfo, SourceView
from clin_compass.modules.knowledge.sections import read_fragment, read_sections

log = structlog.get_logger()


@dataclass(frozen=True)
class SourceCatalog:
    """Источники манифеста по id и регистрация препарата по id его инструкции."""

    sources: Mapping[str, SourceInfo]
    registrations: Mapping[str, RegistryInfo]

    @classmethod
    def from_knowledge(
        cls, knowledge: Knowledge, *, registries: Mapping[str, Registry] | None = None
    ) -> "SourceCatalog":
        """Справочник из штатной загрузки knowledge/. Черновики карточек не входят.

        `registries` — уже прочитанные снимки ГРЛС по id источника: api берёт их
        у справочника препаратов, чтобы не разбирать файл второй раз. Снимок, которого
        там нет, читается из knowledge/.
        """
        snapshots = dict(registries or {})
        registrations: dict[str, RegistryInfo] = {}
        for drug in knowledge.drugs.values():
            if drug.status != "reviewed" or drug.label in registrations:
                continue
            registration = _registration(knowledge, drug, snapshots)
            if registration is not None:
                registrations[drug.label] = registration
        return cls(
            sources={s.id: _info(s) for s in knowledge.sources.values()},
            registrations=registrations,
        )


def _info(source: Source) -> SourceInfo:
    return SourceInfo(
        id=source.id,
        title=source.title,
        kind=source.kind,
        publisher=source.publisher,
        version=source.version,
        date=source.date,
        url=str(source.url),
        supplementary=source.supplementary,
    )


def _registration(
    knowledge: Knowledge, drug: Drug, registries: dict[str, Registry]
) -> RegistryInfo | None:
    """Номер РУ из карточки и статус по снимку ГРЛС; недостающий снимок читается и
    дописывается в `registries`.

    None — инструкции или снимка нет в манифесте либо номера нет в снимке. Проверка
    knowledge/ это запрещает, поэтому здесь только запись в лог.
    """
    label = knowledge.sources.get(drug.label)
    snapshot = knowledge.sources.get(drug.registration.source.source)
    if label is None or label.kind != "drug_label" or snapshot is None or snapshot.file is None:
        log.warning("knowledge.registration_skipped", drug=drug.id)
        return None
    if snapshot.id not in registries:
        registries[snapshot.id] = load_registry(knowledge.root / "sources" / snapshot.file)
    entry = registries[snapshot.id].by_number(drug.registration.number)
    if entry is None:
        log.warning("knowledge.registration_not_in_snapshot", drug=drug.id, snapshot=snapshot.id)
        return None
    return RegistryInfo(
        reg_number=drug.registration.number, status=entry.status, snapshot_date=snapshot.date
    )


async def view_source(
    session: AsyncSession,
    catalog: SourceCatalog,
    source_id: str,
    *,
    locator: str | None = None,
    fragment_id: UUID | None = None,
) -> SourceView | None:
    """Документ из манифеста и текст: разделы по `locator` или один фрагмент по id.

    Текста нет в индексе — `found: false` со сведениями о документе. Фрагмент по id
    отдаётся и из прежней версии документа: на него ссылается утверждение справки.

    Returns:
        None — источника нет в манифесте, фрагмента нет или он из другого источника.

    Raises:
        ValueError: Задан не ровно один из `locator` и `fragment_id`.
    """
    source = catalog.sources.get(source_id)
    if source is None:
        return None
    if locator is not None and fragment_id is None:
        sections = await read_sections(session, source_id, locator)
    elif fragment_id is not None and locator is None:
        section = await read_fragment(session, source_id, fragment_id)
        if section is None:
            return None
        sections = [section]
    else:
        raise ValueError("нужен ровно один из locator и fragment_id")
    return SourceView(
        source=source,
        registry=catalog.registrations.get(source_id),
        locator=locator,
        found=bool(sections),
        sections=sections,
    )
