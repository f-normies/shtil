"""Перекрёстные проверки `signal-decisions.yaml` (спека #93).

Отдельно от `checks.py`: тот уже длиннее 400 строк. Общие помощники берутся оттуда.
"""

from collections.abc import Iterator
from itertools import pairwise
from typing import TYPE_CHECKING

from clin_compass_knowledge.checks import _check_primary, _check_refs, _check_unique
from clin_compass_knowledge.issues import Issue
from clin_compass_knowledge.schemas.signal_decisions import THERAPY_SIGNAL_KINDS, Therapy

if TYPE_CHECKING:
    from clin_compass_knowledge.loader import Knowledge

FILE = "signal-decisions.yaml"
# Причина «Другое»: без неё врач не сможет отказаться по причине, которой нет в списке.
OTHER_REASON = "other"


def check_signal_decisions(kn: "Knowledge") -> Iterator[Issue]:
    """Ошибки файла решений; файла нет — проверять нечего."""
    data = kn.signal_decisions
    if data is None:
        return
    yield from _check_refs(kn, FILE, data)
    decisions = data.safety.decisions
    yield from _check_unique(
        FILE, ((f"safety.decisions[{i}].id", d.id) for i, d in enumerate(decisions))
    )
    for i, decision in enumerate(decisions):
        yield from _check_primary(kn, FILE, f"safety.decisions[{i}].sources", decision.sources)
    yield from _check_primary(kn, FILE, "safety.no_contact.sources", data.safety.no_contact.sources)
    yield from _check_therapy(kn, data.therapy)
    reasons = data.referral_decline.reasons
    yield from _check_unique(
        FILE, ((f"referral_decline.reasons[{i}].id", r.id) for i, r in enumerate(reasons))
    )
    for i, reason in enumerate(reasons):
        yield from _check_primary(
            kn, FILE, f"referral_decline.reasons[{i}].sources", reason.sources
        )
    if all(r.id != OTHER_REASON for r in reasons):
        yield Issue(FILE, "referral_decline.reasons", "нужна причина other («Другое»)")
    yield from _check_snooze(data.snooze.max_days, data.snooze.presets_days)


def _check_therapy(kn: "Knowledge", therapy: Therapy) -> Iterator[Issue]:
    """Уникальные id, основной источник у варианта с источниками, вариант на каждый вид."""
    decisions = therapy.decisions
    yield from _check_unique(
        FILE, ((f"therapy.decisions[{i}].id", d.id) for i, d in enumerate(decisions))
    )
    for i, decision in enumerate(decisions):
        if decision.sources:
            yield from _check_primary(kn, FILE, f"therapy.decisions[{i}].sources", decision.sources)
    covered = {kind for d in decisions for kind in d.applies_to}
    for kind in THERAPY_SIGNAL_KINDS:
        if kind not in covered:
            yield Issue(FILE, "therapy.decisions", f"нет варианта для {kind}")


def _check_snooze(max_days: int, presets: tuple[int, ...]) -> Iterator[Issue]:
    path = "snooze.presets_days"
    if any(a >= b for a, b in pairwise(presets)):
        yield Issue(FILE, path, "варианты идут строго по возрастанию")
    for days in presets:
        if days > max_days:
            yield Issue(FILE, path, f"{days} больше предела max_days {max_days}")
