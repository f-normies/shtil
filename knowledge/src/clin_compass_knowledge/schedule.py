"""Перекрёстные проверки `scale-schedule.yaml` (спека #111).

Отдельно от `checks.py`: тот уже длиннее 400 строк. Общие помощники берутся оттуда.
"""

from collections.abc import Iterator
from typing import TYPE_CHECKING

from clin_compass_knowledge.checks import _check_primary, _check_refs, _check_unique
from clin_compass_knowledge.issues import Issue

if TYPE_CHECKING:
    from clin_compass_knowledge.loader import Knowledge

FILE = "scale-schedule.yaml"


def check_scale_schedule(kn: "Knowledge") -> Iterator[Issue]:
    """Ошибки файла расписания; файла нет — проверять нечего."""
    data = kn.scale_schedule
    if data is None:
        return
    yield from _check_refs(kn, FILE, data)
    yield from _check_unique(
        FILE, ((f"scales[{i}].scale", s.scale) for i, s in enumerate(data.scales))
    )
    yield from _check_unique(
        FILE,
        ((f"allowed_period_days[{i}]", str(d)) for i, d in enumerate(data.allowed_period_days)),
    )
    for i, days in enumerate(data.allowed_period_days):
        if days <= data.grace_days:
            msg = f"{days} не больше срока пропуска grace_days {data.grace_days}"
            yield Issue(FILE, f"allowed_period_days[{i}]", msg)
    for i, entry in enumerate(data.scales):
        if entry.period_days <= data.grace_days:
            msg = f"{entry.period_days} не больше срока пропуска grace_days {data.grace_days}"
            yield Issue(FILE, f"scales[{i}].period_days", msg)
        elif entry.period_days not in data.allowed_period_days:
            msg = f"{entry.period_days} нет среди allowed_period_days"
            yield Issue(FILE, f"scales[{i}].period_days", msg)
        yield from _check_primary(kn, FILE, f"scales[{i}].source", (entry.source,))
        scale = kn.scales.get(entry.scale)
        if scale is None:
            if f"scales/{entry.scale}" not in kn.failed:
                yield Issue(FILE, f"scales[{i}].scale", f"нет шкалы {entry.scale}")
        elif scale.respondent != "patient":
            yield Issue(FILE, f"scales[{i}].scale", f"шкалу {entry.scale} заполняет не пациент")
