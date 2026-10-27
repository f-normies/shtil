"""Перекрёстные проверки между файлами (спека, раздел 5).

Файл длиннее 400 строк. Здесь все проверки связей между файлами `knowledge/`: их по порядку
вызывает `check`, и почти все пользуются общими помощниками `_check_unique` и `_check_refs`.
"""

import re
from collections.abc import Iterable, Iterator, Sequence
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from pydantic import BaseModel

from clin_compass_knowledge.grls import load_registry
from clin_compass_knowledge.issues import Issue
from clin_compass_knowledge.rules import evaluate as evaluate_rule
from clin_compass_knowledge.safety import case_facts, evaluate
from clin_compass_knowledge.schemas.case import Case
from clin_compass_knowledge.schemas.common import SourceRef
from clin_compass_knowledge.schemas.drug import Drug
from clin_compass_knowledge.schemas.rule import Condition, Rule
from clin_compass_knowledge.schemas.scale import Scale, Score
from clin_compass_knowledge.schemas.scenario import Scenario
from clin_compass_knowledge.schemas.source import Source
from clin_compass_knowledge.scoring import score_range

if TYPE_CHECKING:
    from clin_compass_knowledge.loader import Knowledge

MANIFEST = "sources/manifest.yaml"
# Метка манифеста в `Knowledge.failed`: он есть, но не прошёл схему или не читается.
MANIFEST_FAILED = "sources/manifest"
# Что обрезается по краям цитаты перед поиском в тексте.
_QUOTE_EDGES = " \"'«»“”„.,;:!?…"


def _source_refs(obj: object, path: str = "") -> Iterator[tuple[str, SourceRef]]:
    """Все ссылки на источники внутри объекта с путями до них."""
    if isinstance(obj, SourceRef):
        yield path, obj
    elif isinstance(obj, BaseModel):
        for name in type(obj).model_fields:
            yield from _source_refs(getattr(obj, name), f"{path}.{name}" if path else name)
    elif isinstance(obj, tuple):
        for i, child in enumerate(obj):
            yield from _source_refs(child, f"{path}[{i}]")


def _check_unique(file: str, pairs: Iterable[tuple[str, str]]) -> Iterator[Issue]:
    """Повторы значений: ошибка на каждом повторе, первое вхождение без ошибки."""
    seen: set[str] = set()
    for path, value in pairs:
        if value in seen:
            yield Issue(file, path, f"повторяется {value}")
        seen.add(value)


def _check_manifest(kn: "Knowledge") -> Iterator[Issue]:
    ids = ((f"sources[{i}].id", src.id) for i, src in enumerate(kn.manifest))
    yield from _check_unique(MANIFEST, ids)
    for i, src in enumerate(kn.manifest):
        if src.file is not None:
            parts = PurePosixPath(src.file).parts
            if len(parts) < 2 or parts[0] != "files" or ".." in parts:
                msg = "копия лежит в sources/files/: путь вида files/<имя>"
                yield Issue(MANIFEST, f"sources[{i}].file", msg)
            elif not (kn.root / "sources" / src.file).is_file():
                yield Issue(MANIFEST, f"sources[{i}].file", f"нет файла sources/{src.file}")
        if src.supplementary and src.kind != "other":
            yield Issue(
                MANIFEST,
                f"sources[{i}].supplementary",
                "дополнением может быть только источник kind: other",
            )


def _check_refs(kn: "Knowledge", file: str, obj: BaseModel) -> Iterator[Issue]:
    if MANIFEST_FAILED in kn.failed:
        return
    known = kn.sources
    for path, ref in _source_refs(obj):
        if ref.source not in known:
            yield Issue(file, f"{path}.source", f"нет источника {ref.source} в манифесте")


def _supplementary(src: Source | None) -> bool:
    """Дополнительный источник. Пометка у `kind` не `other` — ошибка манифеста.

    О ней сообщает проверка манифеста, поэтому здесь такой источник считается основным.
    """
    return src is not None and src.supplementary and src.kind == "other"


def _check_primary(
    kn: "Knowledge", file: str, path: str, refs: Sequence[SourceRef]
) -> Iterator[Issue]:
    """Международный источник — только дополнение: рядом нужен основной."""
    if MANIFEST_FAILED in kn.failed:
        return
    found = [kn.sources.get(ref.source) for ref in refs]
    if found and all(_supplementary(src) for src in found):
        yield Issue(file, path, "только дополнительные источники: нужен основной")


def _check_validation(kn: "Knowledge", file: str, scale: Scale) -> Iterator[Issue]:
    """Шкала ссылается на источник русскоязычной версии.

    Это публикация о валидации. У шкалы, которую заполняет врач, это может быть и
    приложение КР Минздрава с русским текстом и ключом (спека, 4.3).
    """
    primary = list(_check_primary(kn, file, "validation", scale.validation))
    yield from primary
    found = [kn.sources.get(ref.source) for ref in scale.validation]
    # Об основном источнике, неизвестной ссылке или манифесте уже сообщено.
    if primary or MANIFEST_FAILED in kn.failed or None in found:
        return
    kinds = {"scale_validation"}
    msg = "нужна ссылка на публикацию о валидации (kind: scale_validation)"
    if scale.respondent == "clinician":
        kinds.add("clinical_guideline")
        msg += " или на приложение КР с русским текстом шкалы (kind: clinical_guideline)"
    if not any(src is not None and src.kind in kinds for src in found):
        yield Issue(file, "validation", msg)


def _check_scale_ids(file: str, scale: Scale) -> Iterator[Issue]:
    """Пункты, суммы, пункты внутри суммы и коды порогов не повторяются."""
    yield from _check_unique(file, ((f"items[{i}].id", it.id) for i, it in enumerate(scale.items)))
    yield from _check_unique(
        file, ((f"scores[{i}].id", sd.id) for i, sd in enumerate(scale.scores))
    )
    item_ids = {it.id for it in scale.items}
    for si, sd in enumerate(scale.scores):
        # `evaluate` ищет пункт и сумму по одному ключу, поэтому общий id неоднозначен.
        if sd.id in item_ids:
            yield Issue(file, f"scores[{si}].id", f"совпадает с id пункта {sd.id}")
        items = ((f"scores[{si}].items[{ii}]", item_id) for ii, item_id in enumerate(sd.items))
        yield from _check_unique(file, items)
        codes = ((f"scores[{si}].bands[{bi}].code", b.code) for bi, b in enumerate(sd.bands))
        yield from _check_unique(file, codes)


def _check_scale(kn: "Knowledge", file: str, scale: Scale) -> Iterator[Issue]:
    yield from _check_scale_ids(file, scale)
    if not scale.license.allowed and scale.status != "draft":
        yield Issue(
            file,
            "license.allowed",
            "шкалу без лицензии можно хранить только со status: draft",
        )
    yield from _check_validation(kn, file, scale)
    for si, sd in enumerate(scale.scores):
        # Повтор пункта искажает сумму: пороги по такой сумме не проверяем.
        skip_coverage = len(set(sd.items)) < len(sd.items)
        for ii, item_id in enumerate(sd.items):
            if scale.item(item_id) is None:
                skip_coverage = True
                yield Issue(file, f"scores[{si}].items[{ii}]", f"нет пункта {item_id}")
        for bi, band in enumerate(sd.bands):
            yield from _check_primary(kn, file, f"scores[{si}].bands[{bi}].source", (band.source,))
            if band.min > band.max:
                skip_coverage = True
                yield Issue(file, f"scores[{si}].bands[{bi}]", "min больше max")
        if not skip_coverage:
            yield from _check_coverage(file, f"scores[{si}].bands", scale, sd)


def _span(a: int, b: int) -> str:
    """Баллы от a до b: `4` или `4–6`."""
    return str(a) if a == b else f"{a}–{b}"


def _check_coverage(file: str, path: str, scale: Scale, sd: Score) -> Iterator[Issue]:
    """Пороги без пропусков и наложений покрывают весь диапазон суммы."""
    low, high = score_range(scale, sd)
    expected = low  # первый балл, который ещё не покрыт
    for bi, band in sorted(enumerate(sd.bands), key=lambda pair: pair[1].min):
        if band.min < low or band.max > high:
            msg = f"порог {band.min}–{band.max} выходит за диапазон суммы {low}–{high}"
            yield Issue(file, f"{path}[{bi}]", msg)
        start, end = max(band.min, low), min(band.max, high)
        if start > end:
            continue  # порог целиком вне диапазона
        if start > expected:
            yield Issue(file, path, f"пропущены баллы {_span(expected, start - 1)}")
        elif start < expected:
            overlap = _span(start, min(expected - 1, end))
            yield Issue(file, path, f"пороги пересекаются на баллах {overlap}")
        expected = max(expected, end + 1)
    if expected <= high:
        yield Issue(file, path, f"пропущены баллы {_span(expected, high)}")


def _leaves(cond: Condition, path: str) -> Iterator[tuple[str, Condition]]:
    if cond.any is not None or cond.all is not None:
        name = "any" if cond.any is not None else "all"
        for i, child in enumerate(cond.any or cond.all or ()):
            yield from _leaves(child, f"{path}.{name}[{i}]")
    else:
        yield path, cond


def _check_rule(kn: "Knowledge", file: str, rule: Rule) -> Iterator[Issue]:
    if rule.kind == "alert" and rule.level != "mandatory":
        yield Issue(file, "level", "у alert уровень всегда mandatory")
    yield from _check_primary(kn, file, "sources", rule.sources)
    broken = False
    for path, leaf in _leaves(rule.when, "when"):
        ok, issues = _check_leaf(kn, file, path, leaf)
        broken = broken or not ok
        yield from issues
    for fires in (True, False):
        if not any(c.fires is fires for c in rule.cases):
            yield Issue(file, "cases", f"нужен хотя бы один случай с fires: {str(fires).lower()}")
    if broken:
        return
    for i, case in enumerate(rule.cases):
        actual = evaluate_rule(rule, case.given)
        if actual is not case.fires:
            verb = "срабатывает" if actual else "не срабатывает"
            yield Issue(
                file,
                f"cases[{i}]",
                f"правило {verb}, а ожидается fires: {str(case.fires).lower()}",
            )


def _check_given(kn: "Knowledge", file: str, rule: Rule) -> Iterator[Issue]:
    """В `given` табличных тестов только существующие шкалы, пункты и суммы."""
    for ci, case in enumerate(rule.cases):
        for scale_id, values in case.given.items():
            path = f"cases[{ci}].given.{scale_id}"
            scale = kn.scales.get(scale_id)
            if scale is None:
                if f"scales/{scale_id}" not in kn.failed:
                    yield Issue(file, path, f"нет шкалы {scale_id}")
                continue
            for key in values:
                if scale.item(key) is None and scale.score_def(key) is None:
                    msg = f"нет пункта или суммы {key} в шкале {scale_id}"
                    yield Issue(file, f"{path}.{key}", msg)


def _check_leaf(kn: "Knowledge", file: str, path: str, leaf: Condition) -> tuple[bool, list[Issue]]:
    """Проверка сравнения: (можно ли исполнять правило, ошибки)."""
    assert leaf.scale is not None and leaf.value is not None
    scale = kn.scales.get(leaf.scale)
    if scale is None:
        if f"scales/{leaf.scale}" in kn.failed:
            return False, []  # о самой шкале уже сообщено
        return False, [Issue(file, f"{path}.scale", f"нет шкалы {leaf.scale}")]
    if leaf.item is not None:
        item = scale.item(leaf.item)
        if item is None:
            msg = f"нет пункта {leaf.item} в шкале {scale.id}"
            return False, [Issue(file, f"{path}.item", msg)]
        low = min(o.value for o in item.options)
        high = max(o.value for o in item.options)
    else:
        sd = scale.score_def(leaf.score or "")
        if sd is None:
            msg = f"нет суммы {leaf.score} в шкале {scale.id}"
            return False, [Issue(file, f"{path}.score", msg)]
        low, high = score_range(scale, sd)
    if not low <= leaf.value <= high:
        msg = f"{leaf.value:g} вне диапазона {low}–{high}"
        return False, [Issue(file, f"{path}.value", msg)]
    return True, []


def quote_in_text(quote: str, text: str) -> bool:
    """Цитата есть в тексте целыми словами, без учёта регистра, пробелов, «ё/е» и кавычек по краям.

    Сравнение то же, что у `quote_in_text` разбора текста в api (спека #32). «плохо» внутри
    «неплохо» не считается.
    """
    needle = _normalize(quote).strip(_QUOTE_EDGES)
    if not needle:
        return False
    return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", _normalize(text)) is not None


def _normalize(value: str) -> str:
    return " ".join(value.lower().replace("ё", "е").split())


def _check_case(file: str, case: Case) -> Iterator[Issue]:
    """Цитата каждого факта есть в тексте истории: по цитатам eval сопоставляет факты."""
    fields = (("risk_factors", case.expected.risk_factors), ("anamnesis", case.expected.anamnesis))
    for field, facts in fields:
        for i, fact in enumerate(facts):
            if not quote_in_text(fact.quote, case.history):
                msg = f"цитаты нет в history.md: «{fact.quote}»"
                yield Issue(file, f"{field}[{i}].quote", msg)


def _check_scenario(kn: "Knowledge", file: str, scenario: Scenario) -> Iterator[Issue]:
    yield from _check_unique(
        file, ((f"steps[{i}].id", step.id) for i, step in enumerate(scenario.steps))
    )
    for si, step in enumerate(scenario.steps):
        for ri, scale_id in enumerate(step.refs.scales):
            if scale_id not in kn.scales and f"scales/{scale_id}" not in kn.failed:
                yield Issue(file, f"steps[{si}].refs.scales[{ri}]", f"нет шкалы {scale_id}")
        for ri, rule_id in enumerate(step.refs.rules):
            if rule_id not in kn.rules and f"rules/{rule_id}" not in kn.failed:
                yield Issue(file, f"steps[{si}].refs.rules[{ri}]", f"нет правила {rule_id}")
        clinical = step.actor == "system" or step.refs.scales or step.refs.rules
        if clinical and not step.sources:
            yield Issue(file, f"steps[{si}].sources", "у клинического шага нужен источник")


def _check_signs(kn: "Knowledge") -> Iterator[Issue]:
    """Источник основной, ранги видов `reviewed` не повторяются: по рангу выбирается заголовок."""
    owners: dict[int, str] = {}
    for sign in kn.signs.values():
        file = f"signs/{sign.id}.yaml"
        yield from _check_refs(kn, file, sign)
        yield from _check_primary(kn, file, "sources", sign.sources)
        if sign.status != "reviewed":
            continue
        owner = owners.setdefault(sign.rank, sign.id)
        if owner != sign.id:
            yield Issue(file, "rank", f"ранг {sign.rank} уже у {owner}")


def check(kn: "Knowledge") -> list[Issue]:
    """Все перекрёстные ошибки загруженного содержимого."""
    issues = list(_check_manifest(kn))
    for scale in kn.scales.values():
        file = f"scales/{scale.id}.yaml"
        issues += _check_refs(kn, file, scale)
        issues += _check_scale(kn, file, scale)
    for rule in kn.rules.values():
        file = f"rules/{rule.id}.yaml"
        issues += _check_refs(kn, file, rule)
        issues += _check_rule(kn, file, rule)
        issues += _check_given(kn, file, rule)
    for case in kn.cases.values():
        issues += _check_case(f"cases/{case.id}/expected.json", case)
    for scenario in kn.scenarios.values():
        file = f"scenarios/{scenario.id}.yaml"
        issues += _check_refs(kn, file, scenario)
        issues += _check_scenario(kn, file, scenario)
    issues += _check_signs(kn)
    for drug in kn.drugs.values():
        file = f"drugs/{drug.id}.yaml"
        issues += _check_refs(kn, file, drug)
        issues += _check_drug(kn, file, drug)
        issues += _check_safety(kn, file, drug)
    for group in kn.drug_groups.values():
        issues += _check_refs(kn, f"drug-groups/{group.id}.yaml", group)
    return issues


def _check_safety(kn: "Knowledge", file: str, drug: Drug) -> Iterator[Issue]:
    """Группы из условий существуют; табличные случаи правил дают ожидаемый исход.

    У правила с проверяемыми условиями нужен случай «да» и случай «нет». Вопрос врачу (`ask`)
    всегда даёт `unknown`, поэтому правило с ним не бывает «нет»: вместо этого нужен случай
    `unknown`. Правило только из вопросов проверять нечем.
    """
    sections = (("contraindications", drug.contraindications), ("cautions", drug.cautions))
    for section, rules in sections:
        yield from _check_unique(file, ((f"{section}[{i}].id", r.id) for i, r in enumerate(rules)))
        for i, rule in enumerate(rules):
            path = f"{section}[{i}]"
            missing = False
            for j, cond in enumerate(rule.when.any):
                group = cond.medication.group if cond.medication else None
                if group is not None and group not in kn.drug_groups:
                    missing = True
                    yield Issue(
                        file, f"{path}.when.any[{j}].medication.group", f"нет группы {group}"
                    )
            if missing:
                continue
            for j, case in enumerate(rule.cases):
                got = evaluate(rule.when, case_facts(case.given), kn.drug_groups).outcome
                if got != case.result:
                    yield Issue(
                        file, f"{path}.cases[{j}]", f"ожидалось {case.result}, получилось {got}"
                    )
            if any(c.ask is None for c in rule.when.any):
                results = {c.result for c in rule.cases}
                asks = any(c.ask is not None for c in rule.when.any)
                for needed in ("yes", "unknown" if asks else "no"):
                    if needed not in results:
                        yield Issue(file, f"{path}.cases", f"нужен случай с result: {needed}")
    yield from _check_unique(
        file, ((f"adverse_reactions[{i}].id", r.id) for i, r in enumerate(drug.adverse_reactions))
    )


def _check_drug(kn: "Knowledge", file: str, drug: Drug) -> Iterator[Issue]:
    """Инструкция — запись `drug_label`, формы схем есть в `forms`, РУ действует в снимке.

    Реакции, которые отмечает пациент, поднимают сигнал врачу: нужны `reaction_actions`.
    """
    yield from _check_unique(file, ((f"forms[{i}].id", f.id) for i, f in enumerate(drug.forms)))
    yield from _check_unique(
        file, ((f"indications[{i}].id", ind.id) for i, ind in enumerate(drug.indications))
    )
    if drug.reaction_actions is None and any(r.patient_reportable for r in drug.adverse_reactions):
        yield Issue(file, "reaction_actions", "нужны действия врача: пациент отмечает реакции")
    forms = {f.id for f in drug.forms}
    for i, ind in enumerate(drug.indications):
        if ind.regimen.form not in forms:
            yield Issue(
                file, f"indications[{i}].regimen.form", f"нет формы {ind.regimen.form} в forms"
            )
    if MANIFEST_FAILED in kn.failed:
        return
    known = kn.sources
    label = known.get(drug.label)
    if label is None:
        yield Issue(file, "label", f"нет источника {drug.label} в манифесте")
    elif label.kind != "drug_label":
        yield Issue(file, "label", f"{drug.label} — не инструкция (kind: drug_label)")
    for i, form in enumerate(drug.forms):
        if form.appearance is None:
            continue
        source = known.get(form.appearance.source.source)
        if source is not None and source.kind != "drug_label":
            ref = form.appearance.source.source
            yield Issue(
                file,
                f"forms[{i}].appearance.source.source",
                f"{ref} — не инструкция (kind: drug_label)",
            )
    ref = drug.registration.source.source
    registry = known.get(ref)
    if registry is None:
        return  # о неизвестном источнике сообщает _check_refs
    if registry.kind != "drug_registry" or registry.file is None:
        yield Issue(
            file,
            "registration.source.source",
            f"{ref} — не реестр (kind: drug_registry с файлом)",
        )
        return
    path = kn.root / "sources" / registry.file
    if not path.is_file():
        return  # о пропавшем файле сообщает _check_manifest
    entry = load_registry(path).by_number(drug.registration.number)
    if entry is None:
        yield Issue(file, "registration.number", f"нет в снимке реестра {ref}")
    elif entry.status != "active":
        yield Issue(file, "registration.number", f"в снимке реестра статус {entry.status}")
