"""Книга правил: правила `reviewed` из knowledge/ и их исполнение по результатам шкал.

Читается один раз при старте api. Ошибка в данных knowledge/ останавливает старт:
правило с ошибкой хуже отсутствия правила.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

import clin_compass_knowledge
import structlog
from clin_compass_knowledge.loader import load_knowledge
from clin_compass_knowledge.rules import evaluate
from clin_compass_knowledge.schemas.common import SourceRef
from clin_compass_knowledge.schemas.patient_help import PatientHelp
from clin_compass_knowledge.schemas.rule import Condition, Results, Rule
from clin_compass_knowledge.schemas.signal_decisions import SignalDecisions
from clin_compass_knowledge.schemas.source import Source

from clin_compass.modules.alerts.schemas import SourceSnapshot

log = structlog.get_logger()


@dataclass(frozen=True)
class Firing:
    """Сработавшее правило: что показать врачу и по каким значениям сработало."""

    rule: Rule
    evidence: dict[str, dict[str, float]]
    sources: list[SourceSnapshot]


class RuleBook:
    """Правила `reviewed` и источники из манифеста. Черновики правил отбрасываются здесь.

    `decisions` — варианты решений и отсрочка (спека #95), `patient_help` — экран помощи
    пациенту (спека #57). Черновик файла не исполняется, как черновик правила; файла нет
    (мини-каталоги тестов) — None.
    """

    def __init__(
        self,
        rules: Iterable[Rule],
        sources: Mapping[str, Source],
        decisions: SignalDecisions | None = None,
        patient_help: PatientHelp | None = None,
    ) -> None:
        self._rules = [r for r in rules if r.status == "reviewed"]
        self._sources = sources
        self._decisions = decisions if decisions and decisions.status == "reviewed" else None
        reviewed_help = patient_help and patient_help.status == "reviewed"
        self._patient_help = patient_help if reviewed_help else None

    @property
    def decisions(self) -> SignalDecisions | None:
        return self._decisions

    @property
    def patient_help(self) -> PatientHelp | None:
        return self._patient_help

    @property
    def rules(self) -> list[Rule]:
        """Исполняемые правила (`reviewed`), копия списка."""
        return list(self._rules)

    @property
    def sources(self) -> Mapping[str, Source]:
        """Источники из манифеста по id: из них берутся название и адрес документа."""
        return self._sources

    @classmethod
    def load(cls, root: Path) -> "RuleBook":
        """Прочитать и проверить каталог knowledge/ `root`.

        Ошибка в данных — `KnowledgeError`. Каталог без `sources/manifest.yaml` тоже ошибка:
        иначе опечатка в пути запустила бы api без единого правила.
        """
        knowledge = load_knowledge(root)
        book = cls(
            knowledge.rules.values(),
            knowledge.sources,
            knowledge.signal_decisions,
            knowledge.patient_help,
        )
        log.info(
            "alerts.rules_loaded",
            root=str(root),
            reviewed=len(book.rules),
            total=len(knowledge.rules),
        )
        return book

    def fire(self, results: Results, submitted_scale: str) -> list[Firing]:
        """Сработавшие правила после отправки шкалы `submitted_scale`.

        Условие проверяется по `results` — последним результатам пациента по каждой шкале,
        поэтому правило по двум шкалам срабатывает, когда приходит вторая из них. Правило
        срабатывает, только если условие истинно благодаря отправленной шкале. Иначе старый
        ответ по другой шкале срабатывал бы заново при каждой отправке.
        """
        return [
            Firing(rule, _evidence(rule, results), self._snapshot(rule))
            for rule in self._rules
            if _fires_on(rule.when, results, submitted_scale)
        ]

    def _snapshot(self, rule: Rule) -> list[SourceSnapshot]:
        # Название и адрес документа копируются в сигнал: история сигнала не меняется,
        # когда манифест обновят.
        return self.snapshot(rule.sources)

    def snapshot(self, refs: Iterable[SourceRef]) -> list[SourceSnapshot]:
        """Ссылки с названием и адресом документа из манифеста."""
        return [
            SourceSnapshot(
                source=ref.source,
                locator=ref.locator,
                title=self._sources[ref.source].title,
                url=str(self._sources[ref.source].url),
            )
            for ref in refs
        ]


def repository_knowledge_dir() -> Path:
    """knowledge/ рабочей копии: пакет лежит в knowledge/src/clin_compass_knowledge."""
    return Path(clin_compass_knowledge.__file__).resolve().parents[2]


def _fires_on(cond: Condition, results: Results, scale: str) -> bool:
    """Условие истинно, и истинность держится на листе по шкале `scale`.

    У `any` достаточно одной истинной ветви по этой шкале. Истинная ветвь по другой
    шкале не в счёт: это старый ответ. У `all` истинны все ветви, и хотя бы одна из них
    держится на этой шкале.
    """
    if cond.any is not None:
        return any(_fires_on(c, results, scale) for c in cond.any)
    if cond.all is not None:
        return evaluate(cond, results) and any(_fires_on(c, results, scale) for c in cond.all)
    return cond.scale == scale and evaluate(cond, results)


def _evidence(rule: Rule, results: Results) -> dict[str, dict[str, float]]:
    """Значения из листьев сработавших ветвей условия, которые есть в `results`.

    У `any` в счёт идут только истинные ветви: значение ложной ветви не причина сигнала,
    а «Причина» в кабинете врача показывает шкалу из evidence (спека #95).
    """
    evidence: dict[str, dict[str, float]] = {}
    for leaf in _true_leaves(rule.when, results):
        key = leaf.item or leaf.score
        # Схему листа проверил пакет knowledge; проверка здесь — для типов и на случай
        # правила, собранного в обход схемы.
        if leaf.scale is None or key is None:
            raise ValueError(f"Rule {rule.id}: comparison without scale or item/score")
        value = results.get(leaf.scale, {}).get(key)
        if value is not None:
            evidence.setdefault(leaf.scale, {})[key] = value
    return evidence


def _true_leaves(cond: Condition, results: Results) -> list[Condition]:
    """Листья ветвей, благодаря которым условие истинно. У `all` — все листья."""
    if cond.any is not None:
        return [leaf for c in cond.any if evaluate(c, results) for leaf in _true_leaves(c, results)]
    if cond.all is not None:
        return [leaf for c in cond.all for leaf in _true_leaves(c, results)]
    return [cond]
