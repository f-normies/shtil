"""Импорт набора вопросов v3 (спека #92, 3.2) в `questions_v3.yaml`.

    uv run --package clin-compass-eval python eval/rag/import_v3.py --database-url … \\
        --questions <вопросы.json> --annotated <эталон.json> [--out <файл>] [--update]

Первый файл — вопросы, написанные без документов; второй — те же вопросы с эталоном.
`--update` — переразметка (спека #105, 3.3): в наборе заменяются только вопросы второго файла,
их деление на подбор и контроль и шапка файла сохраняются.
Вопрос и вводная должны совпасть в обоих файлах. Цитата пункта ищется в фрагментах своего
источника в действующем индексе: без учёта регистра, «ё», кавычек, дефисов и пробелов.
В набор пишутся ключ и отпечаток фрагмента, сама цитата — нет. Пункты, `must_not` и
комментарий не должны повторять фрагмент дословно: 8 слов с буквами подряд.

Любая ошибка — список проблем и код 1; файл набора не пишется.
"""

import argparse
import asyncio
import json
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from dataset import Kind, Split, fingerprint, split_ids
from dataset_v3 import QUESTIONS_V3, QuestionV3, Style, dump_questions_v3, load_questions_v3
from retrieval import Doc

# Имена документов в файлах разметки → `id` источника в манифесте.
DOC_NAMES = {"kr-gtr-2024": "kr-gad-2024", "aviandr-leaflet": "aviandr-leaflet-2026"}
COPY_WORDS = 8
SEED = 92
TEST_SHARE = 0.5
_QUOTES = re.compile(r"[«»“”„\"'\u00ad]")
# Пробелы, дефисы любого вида и «|» между ячейками таблицы при сравнении цитаты не учитываются:
# у разметчика бывает «врачом психиатром» вместо «врачом-психиатром»,
# «**50-200» вместо «** 50-200» и строка таблицы без «|».
_IGNORED = re.compile(r"[\s|\-\u2010-\u2015\u2212]+")
# Слово для проверки на копию — с буквами. Числа порогов и доз — факты,
# текстом документа они не считаются.
_WORD = re.compile(r"\w*[^\W\d_]\w*")
_HEADER = """\
# Набор вопросов для оценки RAG, v3 (спека #92, раздел 3). Вопросы и черновой эталон подготовлены
# LLM вне репозитория: вопросы — без документов, эталон — по документам. Импорт — `import_v3.py`.
# Эталон контрольной половины проверяет медик. Цитат КР здесь нет: условия копирования
# рекомендаций не указаны; у пункта — ключи и отпечатки фрагментов, где разметчик нашёл цитату.
"""


def _squeeze(text: str) -> str:
    return _IGNORED.sub("", _QUOTES.sub("", text.lower().replace("ё", "е")))


def match_quote(quote: str, source_id: str, docs: Mapping[str, Doc]) -> list[str]:
    """Ключи фрагментов источника, в тексте которых есть цитата; пусто — не найдена."""
    needle = _squeeze(quote)
    if not needle:
        return []
    return [
        key
        for key, doc in docs.items()
        if doc.source_id == source_id and needle in _squeeze(doc.text)
    ]


def style_of(question: str) -> Style:
    """`short`, если первая буква вопроса строчная, иначе `full`."""
    first = next((ch for ch in question if ch.isalpha()), "")
    return "short" if first.islower() else "full"


def kind_of(evidence: Sequence[Sequence[str]]) -> Kind:
    """Вид для метрик поиска по фрагментам-доказательствам пунктов."""
    if not evidence:
        return "none"
    return "multi" if len({key for keys in evidence for key in keys}) > 1 else "single"


def _words(text: str) -> list[str]:
    return _WORD.findall(text.lower().replace("ё", "е"))


def _grams(docs: Mapping[str, Doc], n: int) -> set[tuple[str, ...]]:
    grams: set[tuple[str, ...]] = set()
    for doc in docs.values():
        words = _words(doc.text)
        grams.update(tuple(words[i : i + n]) for i in range(len(words) - n + 1))
    return grams


def _copied(texts: Sequence[str], grams: set[tuple[str, ...]], n: int) -> list[str]:
    result = []
    for text in texts:
        words = _words(text)
        if any(tuple(words[i : i + n]) in grams for i in range(len(words) - n + 1)):
            result.append(text)
    return result


def copied_text(texts: Sequence[str], docs: Mapping[str, Doc], n: int = COPY_WORDS) -> list[str]:
    """Тексты, у которых есть `n` слов подряд из какого-нибудь фрагмента."""
    return _copied(texts, _grams(docs, n), n)


def _draft(
    item: Mapping[str, Any],
    docs: Mapping[str, Doc],
    grams: set[tuple[str, ...]],
    problems: list[str],
) -> dict[str, Any]:
    """Вопрос без деления; найденные проблемы дописываются в `problems`."""
    qid = item["id"]
    points = []
    for number, point in enumerate(item["points"], start=1):
        keys: list[str] = []
        for evidence in point["evidence"]:
            source = DOC_NAMES.get(evidence["doc"], evidence["doc"])
            found = match_quote(evidence["quote"], source, docs)
            if not found:
                problems.append(
                    f"{qid}: пункт {number}: цитата не найдена в {source}, "
                    f"раздел {evidence.get('section')}: «{evidence['quote'][:80]}»"
                )
            keys.extend(key for key in found if key not in keys)
        points.append(
            {
                "text": point["text"],
                "critical": point["critical"],
                "evidence": [{"key": k, "fingerprint": fingerprint(docs[k].text)} for k in keys],
            }
        )
    note = (item.get("comment") or "").strip() or None
    own = [p["text"] for p in item["points"]] + list(item["must_not"]) + ([note] if note else [])
    for text in _copied(own, grams, COPY_WORDS):
        problems.append(f"{qid}: текст дословно повторяет фрагмент: «{text[:80]}»")
    evidence = [[e["key"] for e in p["evidence"]] for p in points]
    graded = list(dict.fromkeys(e for p in points for e in map(_pair, p["evidence"])))
    return {
        "id": qid,
        "question": item["question"],
        "patient": item.get("patient") or None,
        "type": item["type"],
        "asker": item["asker"],
        "style": style_of(item["question"]),
        "kind": kind_of(evidence),
        "points": points,
        "must_not": list(item["must_not"]),
        "relevant": [{"key": k, "fingerprint": f, "grade": 2} for k, f in graded],
        "note": note,
    }


def _pair(evidence: Mapping[str, str]) -> tuple[str, str]:
    return evidence["key"], evidence["fingerprint"]


def build_questions(
    plain: Mapping[str, Any],
    annotated: Mapping[str, Any],
    docs: Mapping[str, Doc],
    *,
    seed: int = SEED,
    splits: Mapping[str, Split] | None = None,
) -> tuple[list[QuestionV3], list[str]]:
    """Вопросы v3 с делением на подбор и контроль или список проблем.

    Args:
        plain: Файл вопросов, написанных без документов.
        annotated: Файл тех же вопросов с эталоном.
        docs: Фрагменты действующего индекса по ключу.
        seed: Зерно деления; деление — пополам внутри каждого вида вопроса.
        splits: Деление из прежнего набора: эти вопросы его сохраняют, даже если вид сменился
            при переразметке. Делятся только остальные.

    Returns:
        Вопросы и пустой список либо пустой список вопросов и проблемы.
    """
    problems: list[str] = []
    first = {q["id"]: q for q in plain["questions"]}
    second = {q["id"]: q for q in annotated["questions"]}
    for qid in sorted(first.keys() ^ second.keys()):
        problems.append(f"{qid}: вопрос есть только в одном из файлов")
    grams = _grams(docs, COPY_WORDS)
    drafts = []
    for qid, item in second.items():
        original = first.get(qid)
        if original is None:
            continue
        if (original["question"], original.get("patient")) != (
            item["question"],
            item.get("patient"),
        ):
            problems.append(f"{qid}: вопрос изменён после файла без документов")
        drafts.append(_draft(item, docs, grams, problems))
    if problems:
        return [], problems
    known = dict(splits or {})
    fresh = [(d["id"], d["type"]) for d in drafts if d["id"] not in known]
    split = {**split_ids(fresh, test_share=TEST_SHARE, seed=seed), **known}
    return [QuestionV3.model_validate({**d, "split": split[d["id"]]}) for d in drafts], []


def replace_questions(
    existing: Sequence[QuestionV3], updated: Sequence[QuestionV3]
) -> list[QuestionV3]:
    """Набор, в котором переразмеченные вопросы заменили прежние на тех же местах.

    Raises:
        ValueError: Переразмеченного вопроса нет в наборе.
    """
    by_id = {q.id: q for q in updated}
    unknown = sorted(by_id.keys() - {q.id for q in existing})
    if unknown:
        raise ValueError(f"этих вопросов нет в наборе: {', '.join(unknown)}")
    return [by_id.get(q.id, q) for q in existing]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--questions", required=True)
    parser.add_argument("--annotated", required=True)
    parser.add_argument("--out", default=str(QUESTIONS_V3))
    parser.add_argument(
        "--update",
        action="store_true",
        help="заменить в наборе --out только вопросы из --annotated, их деление сохраняется",
    )
    args = parser.parse_args()

    from tune_io import load_docs

    out = Path(args.out)
    annotated = json.loads(Path(args.annotated).read_text(encoding="utf-8"))
    plain = json.loads(Path(args.questions).read_text(encoding="utf-8"))
    existing = load_questions_v3(out) if args.update else []
    if args.update:
        ids = {q["id"] for q in annotated["questions"]}
        plain = {"questions": [q for q in plain["questions"] if q["id"] in ids]}
    docs = asyncio.run(load_docs(args.database_url))
    splits = {q.id: q.split for q in existing}
    questions, problems = build_questions(plain, annotated, docs, splits=splits)
    if problems:
        print("\n".join(problems), file=sys.stderr)  # noqa: T201
        sys.exit(1)
    header = _HEADER
    if args.update:
        questions = replace_questions(existing, questions)
        header = "".join(
            line + "\n" for line in out.read_text().splitlines() if line.startswith("#")
        )
    dump_questions_v3(questions, out, header=header)
    print(f"{len(questions)} вопросов → {args.out}")  # noqa: T201


if __name__ == "__main__":
    main()
