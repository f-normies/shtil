"""Проверка препарата из справочника по профилю пациента (спека #28, раздел 5; #29, раздел 6).

Без LLM и без БД: на входе препарат, показание, профиль и дата, на выходе список проверок
с источниками. Предложение назначения создаётся, только если ни одна проверка не `fail`.
Противопоказание даёт `fail`, мера предосторожности — `warn`, нехватка данных — `manual`.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Literal

from clin_compass_knowledge.grls import Registry, load_registry
from clin_compass_knowledge.loader import Knowledge, load_knowledge
from clin_compass_knowledge.safety import Facts, Medication, evaluate
from clin_compass_knowledge.schemas.common import SourceRef
from clin_compass_knowledge.schemas.drug import Drug, Indication, SafetyRule
from clin_compass_knowledge.schemas.drug_group import DrugGroup
from clin_compass_knowledge.schemas.source import Source

CheckStatus = Literal["pass", "fail", "warn", "manual"]
_UNMATCHED_DETAIL = "МНН препарата по снимку ГРЛС не определён, группы взаимодействий не проверены"


@dataclass(frozen=True)
class Catalog:
    """Справочник препаратов `reviewed`, их снимки реестра и источники манифеста."""

    drugs: Mapping[str, Drug]
    registries: Mapping[str, Registry]
    sources: Mapping[str, Source]
    groups: Mapping[str, DrugGroup] = field(default_factory=dict)

    @classmethod
    def from_knowledge(cls, knowledge: Knowledge) -> "Catalog":
        """Справочник из результата штатной загрузки `knowledge/`. Черновики не входят."""
        drugs = {d.id: d for d in knowledge.drugs.values() if d.status == "reviewed"}
        # Снимки реестра нужны и без проверенных препаратов: по ним сверяются препараты пациента.
        registries = {
            source.id: load_registry(knowledge.root / "sources" / source.file)
            for source in knowledge.sources.values()
            if source.kind == "drug_registry" and source.file is not None
        }
        return cls(
            drugs=drugs,
            registries=registries,
            sources=knowledge.sources,
            groups=knowledge.drug_groups,
        )

    @property
    def registry(self) -> Registry | None:
        """Снимок ГРЛС для сверки препаратов пациента: реестр справочника."""
        return next(iter(self.registries.values()), None)

    @classmethod
    def load(cls, root: Path) -> "Catalog":
        return cls.from_knowledge(load_knowledge(root))


@dataclass(frozen=True)
class Profile:
    """Что известно о пациенте для проверки: дата рождения, активные диагнозы МКБ-10
    и действующие назначения (id препарата → дата начала)."""

    birth_date: date | None
    diagnoses: frozenset[str]
    prescribed_since: Mapping[str, date] = field(default_factory=dict)
    sex: str | None = None
    # egfr, child_pugh, pregnant, breastfeeding; нет ключа — значение неизвестно.
    values: Mapping[str, object] = field(default_factory=dict)
    medications: tuple[Medication, ...] = ()

    def facts(self, today: date) -> Facts:
        return Facts(
            today=today,
            sex=self.sex,
            diagnoses=self.diagnoses,
            values=self.values,
            medications=self.medications,
        )


@dataclass(frozen=True)
class Check:
    id: str
    title: str
    status: CheckStatus
    detail: str
    source: SourceRef | None
    actions: tuple[str, ...] = ()


@dataclass(frozen=True)
class Regimen:
    """Выбранная схема: разовая доза, кратность, длительность в днях (`None` — без срока)."""

    dose_mg: float
    times_per_day: int
    duration_days: int | None

    @classmethod
    def of(cls, indication: Indication) -> "Regimen":
        """Схема по инструкции для показания."""
        r = indication.regimen
        return cls(r.dose_mg, r.times_per_day, r.duration.days)


def candidates(catalog: Catalog, profile: Profile) -> list[tuple[Drug, Indication]]:
    """Показания, к которым относится диагноз пациента: код показания или его подрубрика.

    Подрубрика попадает в подбор, чтобы врач увидел `fail` с просьбой уточнить код,
    а не ложное «в справочнике нет препарата».
    """
    return [
        (drug, indication)
        for drug in catalog.drugs.values()
        for indication in drug.indications
        if profile.diagnoses & set(indication.icd10) or _subcodes(indication, profile)
    ]


def run_checks(
    drug: Drug, indication: Indication, profile: Profile, catalog: Catalog, today: date
) -> list[Check]:
    """Все проверки препарата по показанию в порядке показа врачу."""
    checks = [
        _registered(drug, catalog),
        _not_prescribed(drug, profile),
        _indication(indication, profile),
        _age(drug, profile, today),
    ]
    checks += [
        Check(
            id=f"manual-{i}",
            title=f"Проверьте: {text}",
            status="manual",
            detail="по коду МКБ-10 не проверяется, условие подтверждает врач",
            source=indication.source,
        )
        for i, text in enumerate(indication.manual)
    ]
    checks += safety_checks(drug, profile, catalog, today)
    return checks


def safety_checks(drug: Drug, profile: Profile, catalog: Catalog, today: date) -> list[Check]:
    """Противопоказания и меры предосторожности, затем препараты без МНН.

    Эти же проверки повторяются по действующим назначениям при новых данных пациента.
    """
    facts = profile.facts(today)
    # Препарат без МНН важен, пока он мог бы попасть под условие «N дней после отмены».
    window = max(
        (
            c.medication.within_days or 0
            for rule in (*drug.contraindications, *drug.cautions)
            for c in rule.when.any
            if c.medication is not None
        ),
        default=0,
    )
    checks = [
        _rule_check(f"contra:{rule.id}", rule, "fail", facts, catalog)
        for rule in drug.contraindications
    ]
    checks += [
        _rule_check(f"caution:{rule.id}", rule, "warn", facts, catalog) for rule in drug.cautions
    ]
    unmatched: dict[str, Check] = {}
    for m in profile.medications:
        if m.inn is not None or not _in_window(m, today, window):
            continue
        # id по названию: повторная проверка отличает новый препарат от известного.
        # Название, внесённое дважды (отменён и внесён снова), — одна проверка.
        check_id = f"unmatched:{m.name.strip().lower()}"
        unmatched.setdefault(
            check_id,
            Check(
                id=check_id,
                title=f"«{m.name.strip()}» не сопоставлен с МНН: проверьте взаимодействия сами",
                status="manual",
                detail=_UNMATCHED_DETAIL,
                source=SourceRef(source=drug.label, locator="4.5"),
                actions=("Сверить взаимодействия по инструкции",),
            ),
        )
    return checks + list(unmatched.values())


def _in_window(m: Medication, today: date, window: int) -> bool:
    """Принимает сейчас или отменён не раньше `window` дней назад, последний день включительно."""
    if m.stopped_on is None or m.stopped_on > today:
        return True
    return window > 0 and (today - m.stopped_on).days <= window


def years_text(n: int) -> str:
    """«21 год», «34 года», «45 лет»."""
    tens, ones = n % 100, n % 10
    if 11 <= tens <= 14:
        return f"{n} лет"
    if ones == 1:
        return f"{n} год"
    return f"{n} года" if 2 <= ones <= 4 else f"{n} лет"


def can_propose(checks: list[Check]) -> bool:
    return all(c.status != "fail" for c in checks)


def is_off_label(indication: Indication, regimen: Regimen) -> bool:
    """Схема вне инструкции: другая доза или кратность, длительность вне курса."""
    label = indication.regimen
    if regimen.dose_mg != label.dose_mg or regimen.times_per_day != label.times_per_day:
        return True
    duration = label.duration
    if duration.days is None:
        return False
    longest = duration.max_days or duration.days
    days = regimen.duration_days
    return days is None or not duration.days <= days <= longest


def age_on(birth: date, today: date) -> int:
    """Полных лет на дату. Родившийся 29 февраля становится старше 1 марта."""
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))


def _registered(drug: Drug, catalog: Catalog) -> Check:
    number = drug.registration.number
    registry = catalog.registries.get(drug.registration.source.source)
    entry = registry.by_number(number) if registry is not None else None
    if entry is None:
        status: CheckStatus = "fail"
        detail = f"{number}: нет в снимке реестра"
    elif entry.status != "active":
        status, detail = "fail", f"{number}: статус в реестре — {entry.status}"
    else:
        status, detail = "pass", f"{number}, действует"
    return Check("registered", "Зарегистрирован в РФ", status, detail, drug.registration.source)


def _not_prescribed(drug: Drug, profile: Profile) -> Check:
    """Второе действующее назначение того же препарата — двойная доза.

    Это проверка данных системы, а не правило инструкции: источника у неё нет.
    """
    since = profile.prescribed_since.get(drug.id)
    if since is None:
        return Check(
            "not_prescribed", "Сейчас не назначен", "pass", "действующего назначения нет", None
        )
    detail = f"уже назначен с {since:%d.%m.%Y}"
    return Check("not_prescribed", "Сейчас не назначен", "fail", detail, None)


def _indication(indication: Indication, profile: Profile) -> Check:
    codes = ", ".join(sorted(profile.diagnoses & set(indication.icd10)))
    title = f"Показание: {indication.title}"
    if not codes:
        detail = _not_listed(indication, profile)
        return Check("indication", title, "fail", detail, indication.source)
    if indication.requires_any and not profile.diagnoses & set(indication.requires_any):
        need = " или ".join(indication.requires_any)
        return Check(
            "indication", title, "fail", f"диагноз {codes}, но нет {need}", indication.source
        )
    extra = sorted(profile.diagnoses & set(indication.requires_any))
    detail = f"диагноз {', '.join([codes, *extra])}"
    return Check("indication", title, "pass", detail, indication.source)


def _not_listed(indication: Indication, profile: Profile) -> str:
    """Почему ни один диагноз пациента не совпал с кодами показания.

    Подрубрика кода показания — не совпадение: инструкция может не покрывать её
    (F43.22 — вид расстройства адаптации, который инструкция не называет). Решает медик в карточке.
    """
    listed = ", ".join(indication.icd10)
    found = sorted(_subcodes(indication, profile))
    if not found:
        return f"нет диагноза {' или '.join(indication.icd10)}"
    if len(found) == 1:
        return (
            f"Код {found[0]} не указан в показаниях инструкции (указаны: {listed}). "
            "Уточните код диагноза"
        )
    return (
        f"Коды {', '.join(found)} не указаны в показаниях инструкции (указаны: {listed}). "
        "Уточните коды диагнозов"
    )


def _subcodes(indication: Indication, profile: Profile) -> set[str]:
    """Диагнозы пациента — подрубрики кода показания, которых в показании нет."""
    return {
        code
        for code in profile.diagnoses - set(indication.icd10)
        if any(code.replace(".", "").startswith(c.replace(".", "")) for c in indication.icd10)
    }


def _age(drug: Drug, profile: Profile, today: date) -> Check:
    minimum = drug.age_min.years
    title = f"Возраст от {minimum} лет"
    if profile.birth_date is None:
        return Check("age", title, "manual", "дата рождения не указана", drug.age_min.source)
    years = age_on(profile.birth_date, today)
    status: CheckStatus = "pass" if years >= minimum else "fail"
    return Check("age", title, status, years_text(years), drug.age_min.source)


def _rule_check(
    check_id: str, rule: SafetyRule, hit: CheckStatus, facts: Facts, catalog: Catalog
) -> Check:
    result = evaluate(rule.when, facts, catalog.groups)
    if result.outcome == "no":
        return Check(check_id, rule.title, "pass", "по данным профиля не выявлено", rule.sources[0])
    detail = "; ".join(result.reasons)
    if result.outcome == "unknown":
        return Check(check_id, rule.title, "manual", detail, rule.sources[0], rule.doctor_actions)
    if result.until is not None:
        detail += f"; можно назначать с {result.until:%d.%m.%Y}"
    return Check(check_id, rule.title, hit, detail, rule.sources[0], rule.doctor_actions)
