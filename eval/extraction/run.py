"""Оценка извлечения из историй болезни (спека #30, раздел 7).

    uv run --package clin-compass-eval python eval/extraction/run.py \\
        --base-url http://<адрес>:8000 [--docparse-url http://<адрес>:8003]

Основной прогон: `history.md` 20 историй `knowledge/cases/` идёт в `analysis.Extractor` —
тот же код, что в worker'е, — как PDF с текстовым слоем. Вызовы в ai_runs не пишутся.

Таблицы markdown становятся строками «ячейка | ячейка», как в тексте документа worker'а.

Прогон с распознаванием (только с `--docparse-url`): case-01, case-08, case-12 и case-17
из `documents/` — PDF с текстовым слоем и PNG одной страницы. Файл проходит путь worker'а:
картинка → PDF, `core/ai/docparse`, сведение со слоем, пределы текста (пустой, длиннее
15 000 знаков), извлечение. Время каждой страницы берётся из записей вызовов docparse.
Файлы отрисованы один раз
из `history.md` в Chromium: `node eval/extraction/render.mjs` (шапка скрипта). Тест
`test_documents.py` проверяет, что они соответствуют текущим историям.

Результат — JSON в `eval/extraction/results/` с моделью, версиями промптов извлечения
и olmOCR и датой снимка ГРЛС; в имени файла — дата и время прогона. На экран — сводка
в markdown.
"""

import argparse
import asyncio
import json
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

import httpx2
from clin_compass.core.ai.docparse import PROMPT_VERSION as OLMOCR_PROMPT
from clin_compass.core.ai.docparse import DocparseClient
from clin_compass.core.ai.llm import MODEL_ALIAS, Llm, build_llm_model
from clin_compass.core.ai.runs import NullRecorder, RunRecord
from clin_compass.core.config import Settings
from clin_compass.modules.ingestion import files
from clin_compass.modules.ingestion.analysis import Extraction, Extractor
from clin_compass.modules.ingestion.document import document_text, flatten_tables, text_problem
from clin_compass_knowledge.grls import load_registry
from clin_compass_knowledge.loader import load_knowledge
from clin_compass_knowledge.schemas.case import Case

from extraction_metrics import KNOWLEDGE, CaseScore, aggregate, load_cases, score_case

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
DOCUMENTS = HERE / "documents"
OCR_CASES = ("case-01", "case-08", "case-12", "case-17")
_TYPES = {".pdf": files.PDF, ".png": files.PNG}
# Как у узла docparse в настройках по умолчанию: страница — до ~30 с, с запасом.
_DOCPARSE_TIMEOUT = 300.0


def extractor_for(base_url: str, model: str = MODEL_ALIAS) -> Extractor:
    """Извлечение моделью `model` на узле `base_url` со снимком ГРЛС из knowledge/."""
    # БД скрипту не нужна; адрес-заглушка, как в eval/freetext.
    settings = Settings(database_url="postgresql+asyncpg://unused/none", llm_urls=(base_url,))
    registry = load_registry(KNOWLEDGE / "sources" / "files" / "grls.csv")
    return Extractor(Llm(build_llm_model(settings, model=model), NullRecorder()), registry)


Problem = Literal["empty_text", "text_too_long"]


class PageTimes:
    """Время страниц docparse из записей вызовов: удачные запросы, в секундах."""

    def __init__(self) -> None:
        self.seconds: list[float] = []

    async def record(self, run: RunRecord) -> UUID:
        """Запоминает время удачного запроса страницы; запись в базу не нужна."""
        if run.role == "docparse" and run.status == "ok":
            self.seconds.append(run.latency_ms / 1000)
        return uuid4()


def result_path(out: Path, model: str, version: str, now: datetime) -> Path:
    """Файл результата: дата и время прогона, модель, версия промпта."""
    return out / f"{now:%Y-%m-%d-%H%M%S}-{model}-{version}.json"


async def main_run(extractor: Extractor, cases: list[Case]) -> tuple[list[CaseScore], list[float]]:
    """Истории как текст PDF со слоем: оценка и время извлечения, с."""
    scores, times = [], []
    for case in cases:
        started = time.perf_counter()
        found = await extractor.extract(flatten_tables(case.history), ocr=False)
        times.append(time.perf_counter() - started)
        scores.append(score_case(case.id, case.expected, found))
    return scores, times


async def ocr_case(
    extractor: Extractor, docparse: DocparseClient, data: bytes, content_type: str
) -> tuple[Extraction | None, Problem | None, list[str]]:
    """Файл по пути worker'а: ответ модели или причина, по которой worker его не отдал бы."""
    pdf = files.to_pdf(data, content_type)
    parsed = await docparse.parse_pdf(pdf, task="eval.extraction")
    text = document_text(parsed.pages)
    sources = [p.source for p in text.pages]
    problem = text_problem(text.text)
    if problem is not None:
        return None, problem, sources
    return await extractor.extract(text.text, ocr=text.ocr), None, sources


async def ocr_run(
    extractor: Extractor, docparse: DocparseClient, times: PageTimes, cases: dict[str, Case]
) -> dict[str, Any]:
    """Файлы `documents/` по пути worker'а; метрики отдельно для PDF и для PNG.

    Документ, который worker не отдал бы модели, считается пропуском всех эталонных фактов.
    """
    result: dict[str, Any] = {}
    for suffix, content_type in _TYPES.items():
        scores, extract_times, sources, problems = [], [], {}, {}
        first_page = len(times.seconds)
        for case_id in OCR_CASES:
            data = (DOCUMENTS / f"{case_id}{suffix}").read_bytes()
            started = time.perf_counter()
            found, problem, sources[case_id] = await ocr_case(
                extractor, docparse, data, content_type
            )
            if found is None:
                problems[case_id] = problem
                found = Extraction(None, (), (), (), 0, uuid4())
            else:
                extract_times.append(time.perf_counter() - started)
            scores.append(score_case(case_id, cases[case_id].expected, found))
        result[suffix.removeprefix(".")] = {
            "metrics": aggregate(scores),
            "page_sources": sources,
            "problems": problems,
            "latency": {
                "document": _latency(extract_times),
                "page": _latency(times.seconds[first_page:]),
            },
        }
    return result


def _latency(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    ordered = sorted(values)
    p95 = ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))]
    return {"median_s": round(statistics.median(ordered), 2), "p95_s": round(p95, 2)}


def _grls_version() -> str:
    manifest = load_knowledge(KNOWLEDGE).sources
    return manifest["grls"].version if "grls" in manifest else "нет в манифесте"


def _table(metrics: dict[str, Any]) -> list[str]:
    lines = ["| Поле | Точность | Полнота | F1 |", "|---|---|---|---|"]
    for name, row in metrics["fields"].items():
        lines.append(f"| {name} | {row['precision']} | {row['recall']} | {row['f1']} |")
    lines += [
        "",
        f"- Препараты, верная доза: {metrics['dose_accuracy']}; режим: "
        f"{metrics['regimen_accuracy']}; статус: {metrics['status_accuracy']}.",
        f"- Коды, предложенные моделью: {metrics['proposed_codes']}, верных "
        f"{metrics['proposed_codes_accuracy']}.",
        f"- Доля отброшенных фактов: {metrics['dropped_share']}.",
        f"- Пропущенные диагнозы: {metrics['missed']['diagnoses'] or 'нет'}.",
        f"- Пропущенные препараты: {metrics['missed']['medications'] or 'нет'}.",
    ]
    return lines


def summary(result: dict[str, Any]) -> str:
    """Сводка для `docs/requirements.md` и презентации."""
    lines = [
        f"Модель `{result['model']}`, промпт `{result['prompts']['extract']}`, "
        f"ГРЛС от {result['grls']}, историй {result['cases']}.",
        "",
        *_table(result["metrics"]),
        f"- Время извлечения: {result['latency']['extract']}.",
    ]
    for kind, run in (result.get("ocr") or {}).items():
        lines += [
            "",
            f"Распознавание, {kind.upper()} ({', '.join(OCR_CASES)}), olmOCR "
            f"`{result['prompts']['olmocr']}`:",
            "",
            *_table(run["metrics"]),
            f"- Время страницы: {run['latency']['page']}; документа целиком: "
            f"{run['latency']['document']}. Не отданы модели: {run['problems'] or 'нет'}.",
        ]
    return "\n".join(lines)


async def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--base-url", required=True, help="корень узла LLM: http://<адрес>:8000")
    parser.add_argument("--docparse-url", help="корень узла docparse: http://<адрес>:8003")
    parser.add_argument("--llm-model", default=MODEL_ALIAS)
    parser.add_argument("--out", type=Path, default=RESULTS)
    args = parser.parse_args()

    extractor = extractor_for(args.base_url, args.llm_model)
    cases = load_cases()
    scores, times = await main_run(extractor, cases)
    result: dict[str, Any] = {
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "model": args.llm_model,
        "prompts": {"extract": extractor.prompt_version, "olmocr": OLMOCR_PROMPT},
        "grls": _grls_version(),
        "cases": len(cases),
        "metrics": aggregate(scores),
        "latency": {"extract": _latency(times)},
        "per_case": {s.case_id: s.fields for s in scores},
    }
    if args.docparse_url:
        timeout = httpx2.Timeout(_DOCPARSE_TIMEOUT, connect=5.0)
        times = PageTimes()
        docparse = DocparseClient((args.docparse_url,), times, timeout=timeout)
        result["ocr"] = await ocr_run(extractor, docparse, times, {c.id: c for c in cases})
    args.out.mkdir(parents=True, exist_ok=True)
    path = result_path(args.out, args.llm_model, extractor.prompt_version, datetime.now(UTC))
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(summary(result))  # noqa: T201
    print(f"\nРезультат: {path}")  # noqa: T201


if __name__ == "__main__":
    asyncio.run(main())
