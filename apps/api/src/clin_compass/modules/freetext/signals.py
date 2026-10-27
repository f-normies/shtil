"""Подъём сигнала по признакам риска из текста (спека #32, раздел 6.2).

Заголовок и действия врача берутся у самого тяжёлого найденного вида (меньший `rank`).
Повтор того же или менее тяжёлого вида привязывается к открытому AI-сигналу пациента:
иначе врач перестаёт замечать сигналы. Более тяжёлый вид всегда даёт новый сигнал
и событие врачу. Модуль только поднимает сигналы: из alerts вызываются `raise_signal`
и `list_for_patient`, функций снять или понизить сигнал там нет.
"""

from collections.abc import Mapping, Sequence
from typing import Any, Literal
from uuid import UUID

from clin_compass_knowledge.schemas.common import SourceRef
from clin_compass_knowledge.schemas.sign import Sign
from clin_compass_knowledge.schemas.source import Source
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from clin_compass.modules.alerts import service as alerts
from clin_compass.modules.alerts.schemas import AlertOut
from clin_compass.modules.freetext.analysis import FoundSign
from clin_compass.modules.freetext.models import Analysis

Stage = Literal["check_risk", "extract"]


async def raise_for_signs(
    session: AsyncSession,
    signs: Mapping[str, Sign],
    sources: Mapping[str, Source],
    analysis: Analysis,
    found: Sequence[FoundSign],
    *,
    stage: Stage,
    run_id: UUID,
) -> None:
    """Поднять сигнал или дописать признаки к существующему. Коммитит вызывающий.

    1. Этот текст уже поднял сигнал того же или более тяжёлого вида: признаки дописываются
       к самому тяжёлому новому из них. Все взяты в работу или решены — признаки только
       хранятся в разборе: врач уже занялся этим текстом.
    2. У пациента нерешённый AI-сигнал того же или более тяжёлого вида: дописать к нему.
    3. Иначе новый сигнал.
    """
    if not found:
        return
    primary = signs[min(found, key=lambda s: signs[s.kind].rank).kind]
    evidence = _evidence(signs, sources, analysis, found, primary, stage)
    # Сигналы пациента пишутся по очереди: два текста, разобранные одновременно,
    # иначе оба не увидели бы открытого сигнала и создали бы два.
    lock_key = func.hashtextextended(f"freetext.signals:{analysis.patient_id}", 0)
    await session.execute(select(func.pg_advisory_xact_lock(lock_key)))
    existing = await alerts.list_for_patient(session, patient_id=analysis.patient_id)
    by_id = {str(a.id): a for a in existing}
    own = [
        (signs[kind].rank, by_id[alert_id])
        for kind, alert_id in analysis.alerts.items()
        if kind in signs and signs[kind].rank <= primary.rank and alert_id in by_id
    ]
    if own:
        # Тот же текст: врач уже взял сигнал в работу — признаки только хранятся (спека #95).
        still_open = [pair for pair in own if pair[1].state == "new"]
        if not still_open:
            return
        target: AlertOut | None = min(still_open, key=lambda pair: pair[0])[1]
    else:
        target = _open_signal_to_join(existing, signs, primary)
    raised = await _raise(
        session, primary, analysis, evidence, run_id, alert_id=target.id if target else None
    )
    # raise_signal создаёт новый сигнал, если врач решил цель между чтением и записью.
    analysis.alerts = {**analysis.alerts, primary.id: str(raised.id)}


def _open_signal_to_join(
    existing: list[AlertOut], signs: Mapping[str, Sign], primary: Sign
) -> AlertOut | None:
    """Самый тяжёлый открытый AI-сигнал того же или более тяжёлого вида."""
    candidates = []
    for alert in existing:
        if alert.origin_type != "ai_run" or alert.state == "resolved":
            continue
        kind = alert.triggers[0].evidence.get("kind") if alert.triggers else None
        sign = signs.get(kind) if isinstance(kind, str) else None
        # Вид, которого больше нет среди reviewed, не сравнить по тяжести: новый сигнал.
        if sign is not None and sign.rank <= primary.rank:
            candidates.append((sign.rank, alert))
    return min(candidates, key=lambda pair: pair[0])[1] if candidates else None


async def _raise(
    session: AsyncSession,
    sign: Sign,
    analysis: Analysis,
    evidence: dict[str, Any],
    run_id: UUID,
    *,
    alert_id: UUID | None,
) -> AlertOut:
    return await alerts.raise_signal(
        session,
        patient_id=analysis.patient_id,
        title=sign.title,
        doctor_actions=list(sign.doctor_actions),
        evidence=evidence,
        ai_run_id=run_id,
        alert_id=alert_id,
    )


def _evidence(
    signs: Mapping[str, Sign],
    sources: Mapping[str, Source],
    analysis: Analysis,
    found: Sequence[FoundSign],
    primary: Sign,
    stage: Stage,
) -> dict[str, Any]:
    kinds = dict.fromkeys(s.kind for s in found)
    return {
        "kind": primary.id,
        "analysis_id": str(analysis.id),
        "source_type": analysis.source_type,
        "source_id": str(analysis.source_id),
        "transcript": analysis.transcript,
        "stage": stage,
        "signs": [{"kind": s.kind, "quote": s.quote, "quote_found": s.quote_found} for s in found],
        "sources": {k: [_source(ref, sources) for ref in signs[k].sources] for k in kinds},
    }


def _source(ref: SourceRef, sources: Mapping[str, Source]) -> dict[str, str]:
    """Снимок ссылки с названием и адресом документа, как у сигнала по правилу (#13).

    Валидатор knowledge/ проверяет, что источник есть в манифесте. Без манифеста — только ссылка.
    """
    source = sources.get(ref.source)
    if source is None:
        return ref.model_dump()
    return {**ref.model_dump(), "title": source.title, "url": str(source.url)}
