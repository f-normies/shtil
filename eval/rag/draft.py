"""Черновик вопросов v2 для оценки RAG в стиле врача (спека #70, раздел 3.1).

    uv run --package clin-compass-eval python eval/rag/draft.py \\
        --database-url postgresql+asyncpg://… --llm-url http://<адрес>:8000

Скрипт берёт фрагменты индекса (после `make index`) и просит LLM написать вопросы врача:
`single` — по одному фрагменту, `multi` — по паре соседних фрагментов раздела, `none` — на темы
рядом с корпусом, которых в нём нет. Примерно треть вопросов — со сведениями о синтетическом
пациенте. Вопрос с общей цепочкой из пяти слов с фрагментом пишется заново, после трёх
попыток выбрасывается.

Результат — `questions.draft.yaml` (в git не попадает) с ключами, отпечатками и текстом
фрагментов для проверки. Разметку судьёй и деление делает `tune.py label`.
"""

import argparse
import asyncio
import hashlib
import json
import random
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from clin_compass.core.ai.llm import Llm, build_llm_model
from clin_compass.core.ai.runs import NullRecorder
from clin_compass.core.config import Settings
from clin_compass.core.db import make_engine, make_sessionmaker
from clin_compass.modules.knowledge import service
from clin_compass.modules.knowledge.schemas import Fragment
from pydantic import BaseModel
from pydantic_ai import Agent, NativeOutput
from pydantic_ai.settings import ModelSettings

from dataset import fingerprint, shares_ngram

HERE = Path(__file__).resolve().parent
DRAFT = HERE / "questions.draft.yaml"
PROMPTS = HERE / "prompts"
TASK = "eval.rag.draft"
MAX_ATTEMPTS = 3
COPY_NGRAM = 5
_PATIENT = {True: "добавь", False: "не добавляй"}
_SINGLE = (
    "Документ: {title}. Раздел: {section}.\nСведения о пациенте: {patient}.\n"
    "<фрагмент>\n{text}\n</фрагмент>"
)
_PAIR = (
    "Документ: {title}. Раздел: {section}.\nСведения о пациенте: {patient}.\n"
    "<фрагмент 1>\n{first}\n</фрагмент 1>\n<фрагмент 2>\n{second}\n</фрагмент 2>"
)
_NONE = "Сколько вопросов: {count}."
# Температура выше нуля: повторная попытка после копирования должна дать другой вопрос.
_SETTINGS = ModelSettings(max_tokens=256, temperature=0.5)
_NONE_SETTINGS = ModelSettings(max_tokens=2048, temperature=0.5)


class DraftQuestion(BaseModel):
    question: str


class DraftQuestions(BaseModel):
    questions: list[str]


def _number(fragment: Fragment) -> int:
    return int(fragment.key.rsplit("/", 1)[1])


def _is_label_core(fragment: Fragment) -> bool:
    return fragment.document.kind == "drug_label" and fragment.section.startswith(("4.", "5."))


def pick_single(
    fragments: Sequence[Fragment], *, recommendations: int, other: int, seed: int
) -> list[Fragment]:
    """Фрагменты для вопросов `single`.

    Все фрагменты разделов 4 и 5 ОХЛП, случайные `recommendations` пунктов рекомендаций
    и `other` прочих фрагментов. Выбор зависит только от зерна и ключей.
    """
    ordered = sorted(fragments, key=lambda f: f.key)
    core = [f for f in ordered if _is_label_core(f)]
    recs = [f for f in ordered if f.kind == "recommendation"]
    rest = [f for f in ordered if f.kind != "recommendation" and not _is_label_core(f)]
    rng = random.Random(seed)
    return (
        core
        + rng.sample(recs, min(recommendations, len(recs)))
        + rng.sample(rest, min(other, len(rest)))
    )


def pick_pairs(
    fragments: Sequence[Fragment], *, count: int, seed: int
) -> list[tuple[Fragment, Fragment]]:
    """Пары соседних фрагментов одного раздела для вопросов `multi`."""
    by_key = {f.key: f for f in fragments}
    candidates = []
    for fragment in sorted(fragments, key=lambda f: f.key):
        prefix = fragment.key.rsplit("/", 1)[0]
        following = by_key.get(f"{prefix}/{_number(fragment) + 1}")
        if following is not None:
            candidates.append((fragment, following))
    return random.Random(seed).sample(candidates, min(count, len(candidates)))


def patient_flags(n: int, *, seed: int) -> list[bool]:
    """Ровно треть (с округлением) вопросов получают сведения о пациенте."""
    chosen = set(random.Random(seed).sample(range(n), round(n / 3)))
    return [i in chosen for i in range(n)]


class Drafter:
    """Агенты, которые пишут вопросы врача: по фрагменту, по паре и без ответа в базе."""

    def __init__(self, llm: Llm) -> None:
        self._llm = llm
        single, pair, none = (
            (PROMPTS / name).read_text(encoding="utf-8")
            for name in ("draft_doctor.md", "draft_pair.md", "draft_none.md")
        )
        self._single: Agent[None, DraftQuestion] = Agent(
            output_type=NativeOutput(DraftQuestion), instructions=single, model_settings=_SETTINGS
        )
        self._pair: Agent[None, DraftQuestion] = Agent(
            output_type=NativeOutput(DraftQuestion), instructions=pair, model_settings=_SETTINGS
        )
        self._none: Agent[None, DraftQuestions] = Agent(
            output_type=NativeOutput(DraftQuestions),
            instructions=none,
            model_settings=_NONE_SETTINGS,
        )
        seen = json.dumps(
            {
                "prompts": [single, pair, none],
                "inputs": [_SINGLE, _PAIR, _NONE],
                "schemas": [
                    DraftQuestion.model_json_schema(),
                    DraftQuestions.model_json_schema(),
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        self.prompt_version = f"{TASK}-{hashlib.sha256(seen.encode()).hexdigest()[:8]}"

    async def _ask(
        self, agent: Agent[None, DraftQuestion], prompt: str, texts: list[str]
    ) -> str | None:
        for _ in range(MAX_ATTEMPTS):
            result = await self._llm.run(
                agent, prompt, task=TASK, prompt_version=self.prompt_version
            )
            question = result.output.question.strip()
            if not any(shares_ngram(question, text, n=COPY_NGRAM) for text in texts):
                return question
        return None

    async def single(self, fragment: Fragment, *, patient: bool) -> str | None:
        """Вопрос, ответ на который есть во фрагменте; None — все попытки копировали текст."""
        prompt = _SINGLE.format(
            title=fragment.document.title,
            section=fragment.heading_path,
            patient=_PATIENT[patient],
            text=fragment.text,
        )
        return await self._ask(self._single, prompt, [fragment.text])

    async def pair(self, first: Fragment, second: Fragment, *, patient: bool) -> str | None:
        """Вопрос, для ответа на который нужны оба фрагмента."""
        prompt = _PAIR.format(
            title=first.document.title,
            section=first.heading_path,
            patient=_PATIENT[patient],
            first=first.text,
            second=second.text,
        )
        return await self._ask(self._pair, prompt, [first.text, second.text])

    async def none(self, count: int) -> list[str]:
        """Вопросы на темы рядом с корпусом, ответа на которые в базе нет."""
        result = await self._llm.run(
            self._none, _NONE.format(count=count), task=TASK, prompt_version=self.prompt_version
        )
        return [q.strip() for q in result.output.questions if q.strip()][:count]


def draft_entry(
    number: int, kind: str, question: str, origin: Sequence[Fragment]
) -> dict[str, Any]:
    """Вопрос черновика: ключи и отпечатки исходных фрагментов, их текст — для проверки."""
    entry: dict[str, Any] = {
        "id": f"d-{number:03d}",
        "question": question,
        "kind": kind,
        "origin": [{"key": f.key, "fingerprint": fingerprint(f.text)} for f in origin],
    }
    if origin:
        entry["source_text"] = "\n---\n".join(f.text for f in origin)
    return entry


async def _fragments(database_url: str) -> list[Fragment]:
    engine = make_engine(database_url)
    try:
        async with make_sessionmaker(engine)() as session:
            return await service.list_fragments(session)
    finally:
        await engine.dispose()


def _log(message: str) -> None:
    print(message, file=sys.stderr)  # noqa: T201


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database-url", required=True, help="база после make index")
    parser.add_argument("--llm-url", required=True, help="корень узла LLM: http://<адрес>:8000")
    parser.add_argument("--recommendations", type=int, default=85)
    parser.add_argument("--other", type=int, default=35)
    parser.add_argument("--pairs", type=int, default=20)
    parser.add_argument("--none", type=int, default=20)
    parser.add_argument("--seed", type=int, default=70)
    parser.add_argument("--out", type=Path, default=DRAFT)
    args = parser.parse_args()

    fragments = await _fragments(args.database_url)
    if not fragments:
        _log("В индексе нет фрагментов: сначала make index.")
        return 1
    singles = pick_single(
        fragments, recommendations=args.recommendations, other=args.other, seed=args.seed
    )
    pairs = pick_pairs(fragments, count=args.pairs, seed=args.seed)
    flags = patient_flags(len(singles) + len(pairs), seed=args.seed)
    settings = Settings(database_url=args.database_url, llm_urls=(args.llm_url,))
    drafter = Drafter(Llm(build_llm_model(settings), NullRecorder()))

    entries: list[dict[str, Any]] = []
    dropped = 0
    jobs: list[tuple[str, list[Fragment]]] = [("single", [f]) for f in singles] + [
        ("multi", [a, b]) for a, b in pairs
    ]
    for (kind, origin), patient in zip(jobs, flags, strict=True):
        if kind == "single":
            question = await drafter.single(origin[0], patient=patient)
        else:
            question = await drafter.pair(origin[0], origin[1], patient=patient)
        if question is None:
            dropped += 1
        else:
            entries.append(draft_entry(len(entries) + 1, kind, question, origin))
        _log(f"{len(entries) + dropped}/{len(jobs)} {kind} {origin[0].key}")
    for question in await drafter.none(args.none):
        entries.append(draft_entry(len(entries) + 1, "none", question, []))

    header = (
        f"# Черновик вопросов v2 для eval/rag: {datetime.now(UTC).date()}, "
        f"{drafter.prompt_version}, зерно {args.seed}.\n"
        "# Разметка и деление — tune.py label. source_text — текст фрагментов для проверки;\n"
        "# в questions.yaml он не переносится.\n"
    )
    body = yaml.safe_dump(entries, allow_unicode=True, sort_keys=False, width=100)
    args.out.write_text(header + body, encoding="utf-8")
    _log(f"Черновик: {args.out}, вопросов: {len(entries)}, выброшено за копирование: {dropped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
