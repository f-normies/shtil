"""Оценка разбора дневника и чата на эталонном наборе (спека #32, раздел 7).

    uv run --package clin-compass-eval python eval/freetext/run.py --base-url http://<адрес>:8000 \\
        [--llm-model <имя>]

Скрипт вызывает те же функции, что и worker (`freetext/analysis.py`): быструю проверку риска
и полный разбор, по очереди для каждого текста. Вызовы в ai_runs не пишутся. Результат —
JSON в `eval/freetext/results/` с моделью, версиями промптов и датой снимка ГРЛС, на экран —
сводка в markdown.
"""

import argparse
import asyncio
import json
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from clin_compass.core.ai.llm import MODEL_ALIAS, Llm, build_llm_model
from clin_compass.core.ai.runs import NullRecorder
from clin_compass.core.config import Settings
from clin_compass.modules.freetext.analysis import Analyzer
from clin_compass_knowledge.grls import load_registry
from clin_compass_knowledge.loader import load_knowledge

from metrics import CASES, Case, CaseScore, Prediction, aggregate, load_cases, score_case

HERE = Path(__file__).resolve().parent
KNOWLEDGE = HERE.parents[1] / "knowledge"
RESULTS = HERE / "results"


def analyzer_for(base_url: str, model: str = MODEL_ALIAS) -> Analyzer:
    """Разбор моделью `model` на узле `base_url` с видами признаков и снимком ГРЛС из knowledge/."""
    # БД скрипту не нужна; адрес-заглушка, как в eval/embeddings.
    settings = Settings(database_url="postgresql+asyncpg://unused/none", llm_urls=(base_url,))
    knowledge = load_knowledge(KNOWLEDGE)
    registry = load_registry(KNOWLEDGE / "sources" / "files" / "grls.csv")
    return Analyzer(
        Llm(build_llm_model(settings, model=model), NullRecorder()),
        knowledge.signs.values(),
        registry,
    )


async def run_case(analyzer: Analyzer, case: Case) -> tuple[CaseScore, float, float]:
    """Оценка текста и время двух вызовов, с."""
    started = time.perf_counter()
    risk = await analyzer.check_risk(
        case.text, source_type=case.source_type, transcript=case.transcript
    )
    checked = time.perf_counter()
    extracted = await analyzer.extract(
        case.text, source_type=case.source_type, transcript=case.transcript
    )
    finished = time.perf_counter()
    prediction = Prediction(extracted.findings, risk.signs, extracted.signs, extracted.dropped)
    return score_case(case, prediction), checked - started, finished - checked


def _latency(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    p95 = ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))]
    return {"median_s": round(statistics.median(ordered), 2), "p95_s": round(p95, 2)}


def _grls_version() -> str:
    manifest = load_knowledge(KNOWLEDGE).sources
    return manifest["grls"].version if "grls" in manifest else "нет в манифесте"


def summary(result: dict[str, Any]) -> str:
    """Сводка для README и презентации."""
    m = result["metrics"]
    risk = m["risk"]
    lines = [
        f"Модель `{result['model']}`, промпты `{result['prompts']['risk']}` и "
        f"`{result['prompts']['extract']}`, ГРЛС от {result['grls']}, текстов {result['cases']}.",
        "",
        "| Что | Точность | Полнота | F1 |",
        "|---|---|---|---|",
    ]
    for name, key in (("Риск: быстрая проверка", "check_risk"), ("Риск: полный разбор", "extract")):
        row = risk[key]
        lines.append(f"| {name} | {row['precision']} | {row['recall']} | {row['f1']} |")
    for category, row in m["findings"].items():
        lines.append(
            f"| Находки: {category} | {row['precision']} | {row['recall']} | {row['f1']} |"
        )
    lines += [
        "",
        f"- Тексты с риском, пропущенные обоими этапами: {risk['missed_by_both'] or 'нет'}.",
        f"- Ложные тревоги: {risk['false_alarms'] or 'нет'}.",
        f"- Приверженность, верные события: {m['adherence_accuracy']}.",
        f"- Препараты, верный итог сверки: {m['drugs_accuracy']}; "
        f"промахи: {m['drugs_missed'] or 'нет'}.",
        f"- Доля выдуманных цитат: {m['quotes_not_found_share']}.",
        f"- Время, проверка риска: {result['latency']['check_risk']}; "
        f"полный разбор: {result['latency']['extract']}.",
    ]
    return "\n".join(lines)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", required=True, help="корень узла LLM: http://<адрес>:8000")
    # Другая модель — только для сравнения кандидатов (спека #70, 6.5).
    parser.add_argument("--llm-model", default=MODEL_ALIAS)
    parser.add_argument("--cases", type=Path, default=CASES)
    parser.add_argument("--out", type=Path, default=RESULTS)
    args = parser.parse_args()

    analyzer = analyzer_for(args.base_url, args.llm_model)
    cases = load_cases(args.cases)
    scores, risk_times, extract_times = [], [], []
    for case in cases:
        score, risk_s, extract_s = await run_case(analyzer, case)
        scores.append(score)
        risk_times.append(risk_s)
        extract_times.append(extract_s)
    result = {
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "model": args.llm_model,
        "prompts": {
            "risk": analyzer.risk_prompt_version,
            "extract": analyzer.extract_prompt_version,
        },
        "grls": _grls_version(),
        "cases": len(cases),
        "metrics": aggregate(scores),
        "latency": {"check_risk": _latency(risk_times), "extract": _latency(extract_times)},
    }
    args.out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    prompts = f"{analyzer.risk_prompt_version}-{analyzer.extract_prompt_version}"
    path = args.out / f"{stamp}-{args.llm_model}-{prompts}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(summary(result))  # noqa: T201
    print(f"\nРезультат: {path}")  # noqa: T201


if __name__ == "__main__":
    asyncio.run(main())
