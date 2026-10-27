"""Итоги подбора v3 (спека #92, раздел 7): этап A, загрузка прогонов, таблицы, выборка медику.

Здесь нет вызовов моделей: только выдачи из кэша, записи прогонов и метрики.
"""

import json
import random
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cache import JsonStore
from dataset import relevant_keys
from rag_metrics import paired_interval, recall_at
from run import outcomes
from v3_io import outcome_from_record, passes_stage_a
from v3_metrics import OutcomeV3, question_values, summary_v3
from v3_search import Variant
from v3_world import (
    DEPTH,
    RETRIEVAL,
    REWRITES,
    Item,
    World,
    cached_rewrite,
    load_items,
    rewrite_versions,
)

SAMPLE_SEED = 92
Diff = tuple[float, float, float]


def _outcome(world: World, item: Item, variant: Variant, rewrites: list[str]) -> Any:
    """Итог поиска: `OutcomeV3` без ответа для v3, recall@5 показанных — для v2."""
    keys, scores = world.retrieve(variant, item, rewrites)
    shown = world.shown(keys, scores)
    if item.v3 is not None:
        return OutcomeV3(item.v3, tuple(keys[:DEPTH]), tuple(shown), (), (), None, None, 0, 0.0)
    assert item.v2 is not None
    relevant = relevant_keys(item.v2)
    return recall_at(relevant, shown, 5) if relevant else None


# Этап A ответа не строит: метрики ответа и время в его итогах бессмысленны.
_ANSWER_METRICS = (
    "point_coverage",
    "critical_complete",
    "must_not_share",
    "refusal",
    "false_refusal",
    "context_use",
    "supported_share",
    "quotes_found_share",
    "latency",
)


def _retrieval_only(summary: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in summary.items() if k not in _ANSWER_METRICS}


def _paired(a: Sequence[Any], b: Sequence[Any]) -> Diff:
    kept = [(x, y) for x, y in zip(a, b, strict=True) if x is not None and y is not None]
    return paired_interval([x for x, _ in kept], [y for _, y in kept], seed=92)


def _values(outs: Sequence[OutcomeV3], metric: str) -> list[Any]:
    return [question_values(o)[metric] for o in outs]


def stage_a(
    world: World, items: Sequence[Item], split: str, *, extra: Sequence[str] = ()
) -> dict[str, Any]:
    """Этап A (спека #105, раздел 7): поисковые варианты на v3 и v2 и правило прохода.

    Этап диагностический: в этап B идут все варианты. `extra` — другие поисковые варианты.
    """
    store, versions = JsonStore(REWRITES), rewrite_versions()
    v3 = [i for i in items if i.v3 is not None and i.split == split]
    v2 = [i for i in items if i.v2 is not None and i.split == split and i.v2.kind != "none"]

    def run(variant: Variant) -> tuple[list[OutcomeV3], list[Any]]:
        def one(item: Item) -> Any:
            return _outcome(world, item, variant, cached_rewrite(store, versions, variant, item)[0])

        return [one(i) for i in v3], [one(i) for i in v2]

    runs = {name: run(Variant.from_name(name)) for name in (*RETRIEVAL, *extra)}
    base_v3, base_v2 = runs["base"]
    table: dict[str, Any] = {}
    for name, (outs, v2_recall) in runs.items():
        diffs = {
            m: _paired(_values(outs, m), _values(base_v3, m))
            for m in ("recall_at_5", "context_coverage")
        }
        v2_diff = _paired(v2_recall, base_v2)
        known = [x for x in v2_recall if x is not None]
        table[name] = {
            "v3": _retrieval_only(summary_v3(outs)),
            "v2_recall_at_5": round(sum(known) / len(known), 4) if known else None,
            "diff_vs_base": {**diffs, "v2_recall_at_5": v2_diff},
            "passed": name != "base" and passes_stage_a(diffs, v2_diff),
        }
    return {
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "split": split,
        "questions": {"v3": len(v3), "v2": len(v2)},
        "variants": table,
    }


def stage_a_lines(result: dict[str, Any]) -> list[str]:
    """Строка на вариант этапа A для вывода в терминал."""
    lines = []
    for name, row in result["variants"].items():
        s, d = row["v3"], row["diff_vs_base"]
        lines.append(
            f"{name:12} r@5 {s['recall_at_5']['mean']:.3f} r@20 {s['recall_at_20']['mean']:.3f} "
            f"hit@5 {s['hit_at_5']['mean']:.3f} P@5 {s['precision_at_5']['mean']:.3f} "
            f"ctx {s['context_coverage']['mean']:.3f} "
            f"Δr@5 {d['recall_at_5'][0]:+.3f} [{d['recall_at_5'][1]:+.3f}] "
            f"Δctx {d['context_coverage'][0]:+.3f} [{d['context_coverage'][1]:+.3f}] "
            f"v2 Δr@5 {d['v2_recall_at_5'][0]:+.3f} {'прошёл' if row['passed'] else ''}"
        )
    return lines


def load_run(path: Path) -> tuple[dict[str, Any], list[Any]]:
    """Файл прогона и итоги для метрик: `OutcomeV3` для v3, `AnswerOutcome` для v2.

    Raises:
        ValueError: В прогоне есть ошибки вызова: пустой ответ из-за сбоя засчитался бы
            как отказ.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("errors"):
        raise ValueError(f"{path.name}: в прогоне есть ошибки ({data['errors']}), перезапустите")
    items = {i.id: i for i in load_items(data["set"])}
    if data["set"] == "v3":
        return data, [outcome_from_record(r, items[r["id"]].v3) for r in data["records"]]  # type: ignore[arg-type]
    questions = [items[r["id"]].v2 for r in data["records"]]
    return data, outcomes(data["records"], questions)  # type: ignore[arg-type]


def markdown_table(result: dict[str, Any]) -> str:
    """Таблица сравнения вариантов по правилу 4.4."""
    lines = [
        "| Вариант | Покрытие пунктов | Важные пункты | Опасный ответ | Подтверждено "
        "| Отказ | Ложный отказ | Медиана / p95, с | Кандидат |",
        "|---|---|---|---|---|---|---|---|---|",
    ]

    def cell(item: Any) -> str:
        return f"{item['mean']:.3f}" if item else "—"

    for name, row in result["runs"].items():
        s = row["summary"]
        t = s["latency"]["total"] or {"median_s": "—", "p95_s": "—"}
        lines.append(
            f"| {name} | {cell(s['point_coverage'])} | {cell(s['critical_complete'])} | "
            f"{cell(s['must_not_share'])} | {cell(s['supported_share'])} | {cell(s['refusal'])} | "
            f"{cell(s['false_refusal'])} | {t['median_s']} / {t['p95_s']} | "
            f"{'да' if row['candidate'] else 'нет'} |"
        )
    return "\n".join(lines)


def medic_sample(data: dict[str, Any], count: int) -> str:
    """Markdown со случайными вердиктами судьи пунктов: вопрос, ответ с цитатами, пункт."""
    items = {i.id: i for i in load_items("v3")}
    pairs = [
        (record, n)
        for record in data["records"]
        if record["claims"] and record["points"]
        for n in range(len(record["points"]))
    ]
    chosen = random.Random(SAMPLE_SEED).sample(pairs, min(count, len(pairs)))
    lines = ["# Сверка судьи пунктов (спека #92, 6.3)", ""]
    for number, (record, n) in enumerate(chosen, start=1):
        item = items[record["id"]]
        assert item.v3 is not None
        lines += [f"## {number}. {item.id}", "", f"**Вопрос:** {item.question}"]
        if item.patient:
            lines.append(f"**Пациент:** {item.patient}")
        lines += ["", "**Ответ:**"]
        lines += [f"- {c['statement']} — «{c['quote']}»" for c in record["claims"]]
        verdict = "есть" if record["points"][n] else "нет"
        lines += [
            "",
            f"**Пункт эталона:** {item.v3.points[n].text}",
            f"**Судья:** {verdict}",
            "**Медик:** ",
            "",
        ]
    return "\n".join(lines)
