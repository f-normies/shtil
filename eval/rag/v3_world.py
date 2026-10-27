"""Окружение прогона v3 (спека #92, раздел 7): вопросы, кэш переписывания, движки поиска.

Движков два: по индексу со шкалами и без фрагментов приложения Г. Выдачи берутся из кэша
подбора #70 (`tune_io.CacheSources`): векторы, полнотекстовый поиск и баллы ранжировщика.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import cast

from clin_compass.core.ai.llm import Llm
from clin_compass.core.db import make_engine, make_sessionmaker
from clin_compass.modules.knowledge import service
from clin_compass.modules.knowledge.answering import MAX_FRAGMENTS, within_budget
from clin_compass.modules.knowledge.questions import Questioner
from clin_compass.modules.knowledge.rewriting import Rewriter
from clin_compass.modules.knowledge.schemas import Fragment, Hit

from cache import CACHE, JsonStore, VectorStore, text_key
from dataset import Question, load_questions
from dataset_v3 import QuestionV3, load_questions_v3
from retrieval import Doc, Engine
from tune_io import CacheSources, full_text_rankings, load_docs
from v3_questions import QUESTIONS_CACHE, QuestionIndex, cached_questions, question_text_rankings
from v3_search import SCALE_SECTION, Variant, plan, rank
from variants import Abbreviations

REWRITES = CACHE / "rewrites.json"
# Поисковые варианты этапа A (спека #105, раздел 7): для них считаются векторы, баллы
# ранжировщика и разметка объединённой выдачи.
RETRIEVAL = ("base", "rw=one+rq=question", "d2q", "d2q+rw=one+rq=question")
DEPTH = 20


@dataclass(frozen=True)
class Item:
    """Вопрос любого набора: v3 со сведениями о пациенте или v2 без них."""

    id: str
    question: str
    patient: str | None
    split: str
    v3: QuestionV3 | None
    v2: Question | None


def load_items(which: str) -> list[Item]:
    """Вопросы набора `v3`, `v2` или обоих (`both`)."""
    items = []
    if which in ("v3", "both"):
        items += [Item(q.id, q.question, q.patient, q.split, q, None) for q in load_questions_v3()]
    if which in ("v2", "both"):
        items += [Item(q.id, q.question, None, q.split, None, q) for q in load_questions()]
    return items


def rewrite_versions() -> dict[str, str]:
    """Версия промпта переписывания для каждого режима; модель для неё не нужна."""
    return {m: Rewriter(cast(Llm, None), mode=m).prompt_version for m in ("one", "multi")}  # type: ignore[arg-type]


def rewrite_key(version: str, question: str, patient: str | None) -> str:
    return text_key("rewrite", f"{version}\n{question}\n{patient or ''}")


def cached_rewrite(
    store: JsonStore, versions: dict[str, str], variant: Variant, item: Item
) -> tuple[list[str], float]:
    """Запросы переписывания из кэша и время вызова; без переписывания — пусто и 0."""
    if variant.rewrite not in ("one", "multi"):
        return [], 0.0
    key = rewrite_key(versions[variant.rewrite], item.question, item.patient)
    if store.missing([key]):
        raise KeyError(f"{item.id}: нет запросов переписывания {variant.rewrite}, сначала rewrite")
    entry = store.get(key)
    return list(entry["queries"]), float(entry["latency_s"])


def all_queries(items: Iterable[Item], store: JsonStore, versions: dict[str, str]) -> list[str]:
    """Все запросы поиска и ранжировщика поисковых вариантов для вопросов `items`."""
    queries: list[str] = []
    for item in items:
        for name in RETRIEVAL:
            variant = Variant.from_name(name)
            rewrites, _ = cached_rewrite(store, versions, variant, item)
            searched, rerank_query = plan(variant, item.question, item.patient, rewrites)
            queries += [*searched, rerank_query]
    return list(dict.fromkeys(queries))


@dataclass(frozen=True)
class World:
    """Фрагменты индекса и движки поиска со шкалами (`True`) и без них (`False`)."""

    docs: dict[str, Doc]
    fragments: dict[str, Fragment]
    engines: dict[bool, Engine]
    sources: dict[bool, CacheSources]
    # Поиск по вопросам к фрагментам (спека #105, 5.1); None — вопросы ещё не сгенерированы.
    questions: dict[bool, QuestionIndex] | None = None

    def retrieve(
        self, variant: Variant, item: Item, rewrites: Sequence[str]
    ) -> tuple[list[str], dict[str, float]]:
        """Выдача варианта для вопроса и баллы ранжировщика."""
        searched, rerank_query = plan(variant, item.question, item.patient, rewrites)
        questions = None
        if variant.d2q:
            if self.questions is None:
                raise RuntimeError("нет вопросов к фрагментам: сначала questions и embed")
            questions = self.questions[variant.scales]
        return rank(
            self.engines[variant.scales],
            searched,
            rerank_query,
            min_score=variant.min_score,
            questions=questions,
        )

    def hits(self, keys: Sequence[str], scores: dict[str, float]) -> list[Hit]:
        """Первые `MAX_FRAGMENTS` фрагментов выдачи как `Hit` для ответа."""
        return [
            Hit(
                fragment=self.fragments[k],
                score=0.0,
                vector_rank=None,
                text_rank=None,
                rerank_score=scores.get(k),
            )
            for k in keys[:MAX_FRAGMENTS]
        ]

    def shown(self, keys: Sequence[str], scores: dict[str, float]) -> list[str]:
        """Фрагменты, которые увидит LLM: бюджет контекста — боевой `within_budget`."""
        return [h.fragment.key for h in within_budget(self.hits(keys, scores), MAX_FRAGMENTS)]


def question_version() -> str:
    """Версия промпта вопросов к фрагментам; модель для неё не нужна."""
    return Questioner(cast(Llm, None)).prompt_version


async def build_world(
    database_url: str, queries: Sequence[str], *, strict: bool, with_questions: bool = True
) -> World:
    """Окружение по базе eval; `strict=False` копит недостающие баллы в `sources[*].wanted`.

    `with_questions` — поиск по вопросам к фрагментам, если они есть в кэше для всех фрагментов.
    """
    docs = await load_docs(database_url)
    engine = make_engine(database_url)
    try:
        async with make_sessionmaker(engine)() as session:
            fragments = {f.key: f for f in await service.list_fragments(session)}
    finally:
        await engine.dispose()
    engines, sources = {}, {}
    for scales in (False, True):
        subset = docs if scales else {k: d for k, d in docs.items() if SCALE_SECTION not in k}
        full_text = await full_text_rankings(database_url, subset, {}, queries)
        sources[scales] = CacheSources(
            docs=subset, cache_dir=CACHE, contexts={}, full_text_rankings=full_text, strict=strict
        )
        engines[scales] = Engine(
            docs=subset,
            sources=sources[scales],
            abbreviations=Abbreviations.build([], {}),
            terms={},
        )
    questions = None
    if with_questions:
        by_key = cached_questions(JsonStore(QUESTIONS_CACHE), question_version(), docs)
        vectors = VectorStore(CACHE / "embeddings" / "giga-3b.npz")
        if len(by_key) == len(docs):
            questions = {}
            for scales, engine in engines.items():
                subset = {k: by_key[k] for k in engine.docs}
                rankings = await question_text_rankings(database_url, docs, subset, queries)
                questions[scales] = QuestionIndex.build(subset, vectors, rankings)
    return World(
        docs=docs, fragments=fragments, engines=engines, sources=sources, questions=questions
    )
