"""Черновик вопросов v2 (спека #70, 3.1): выбор фрагментов и генерация в стиле врача.

LLM не вызывается: `FunctionModel` отвечает по сценарию.
"""

import json
from collections import Counter
from uuid import uuid4

import pytest
from clin_compass.core.ai.llm import MODEL_ALIAS, Llm
from clin_compass.core.ai.runs import NullRecorder
from clin_compass.modules.knowledge.schemas import Fragment
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from dataset import fingerprint
from draft import Drafter, draft_entry, patient_flags, pick_pairs, pick_single

TEXT = (
    "Пациентам с тревогой рекомендуется проводить электроэнцефалографию при подозрении на приступы."
)


def _fragment(
    source_id: str, section: str, n: int, kind: str = "text", text: str = TEXT
) -> Fragment:
    return Fragment.model_validate(
        {
            "id": uuid4(),
            "key": f"{source_id}/{section}/{n}",
            "document": {
                "id": uuid4(),
                "source_id": source_id,
                "title": source_id,
                "version": "1",
                "url": "https://example.org",
                "kind": "drug_label" if source_id.endswith("ohlp") else "clinical_guideline",
                "status": "active",
            },
            "section": section,
            "section_title": section,
            "heading_path": section,
            "kind": kind,
            "text": text,
            "uur": "A" if kind == "recommendation" else None,
            "udd": 1 if kind == "recommendation" else None,
        }
    )


def _corpus() -> list[Fragment]:
    label = [_fragment("aviandr-ohlp", s, 1) for s in ("1", "4.1", "4.3", "5.2", "6.1")]
    recs = [_fragment("kr-gad", "doc_3", n, "recommendation") for n in range(1, 21)]
    other = [_fragment("kr-gad", "doc_1", n) for n in range(1, 11)]
    return label + recs + other


def _keys(fragments: list[Fragment]) -> list[str]:
    return [f.key for f in fragments]


def test_pick_single_takes_label_parts_4_5_and_samples_the_rest() -> None:
    picked = pick_single(_corpus(), recommendations=5, other=4, seed=70)

    core = {"aviandr-ohlp/4.1/1", "aviandr-ohlp/4.3/1", "aviandr-ohlp/5.2/1"}
    assert core <= set(_keys(picked))
    kinds = Counter(f.kind for f in picked if f.key not in core)
    assert kinds == {"recommendation": 5, "text": 4}
    assert len(set(_keys(picked))) == len(picked)
    assert _keys(pick_single(_corpus(), recommendations=5, other=4, seed=70)) == _keys(picked)


def test_pick_single_other_includes_label_outside_parts_4_5() -> None:
    picked = pick_single(_corpus(), recommendations=0, other=100, seed=70)

    assert "aviandr-ohlp/6.1/1" in _keys(picked)


def test_pick_pairs_are_neighbours_in_one_section() -> None:
    pairs = pick_pairs(_corpus(), count=6, seed=70)

    assert len(pairs) == 6
    for first, second in pairs:
        assert first.section == second.section
        assert first.document.source_id == second.document.source_id
        assert int(second.key.rsplit("/", 1)[1]) == int(first.key.rsplit("/", 1)[1]) + 1


def test_patient_flags_mark_a_third() -> None:
    flags = patient_flags(30, seed=70)

    assert sum(flags) == 10
    assert flags == patient_flags(30, seed=70)


def _scripted(answers: list[dict[str, object]], seen: list[str]) -> Llm:
    queue = list(answers)

    async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(str(messages[-1].parts[-1].content))  # type: ignore[union-attr]
        return ModelResponse(parts=[TextPart(json.dumps(queue.pop(0), ensure_ascii=False))])

    return Llm(FunctionModel(respond, model_name=MODEL_ALIAS), NullRecorder())


@pytest.mark.anyio
async def test_question_copying_fragment_is_regenerated() -> None:
    seen: list[str] = []
    copying = {"question": "Когда рекомендуется проводить электроэнцефалографию при подозрении?"}
    own = {"question": "Нужна ли ЭЭГ пациенту с ГТР, если были эпизоды потери сознания?"}
    drafter = Drafter(_scripted([copying, own], seen))

    question = await drafter.single(_fragment("kr-gad", "doc_2", 1), patient=False)

    assert question == own["question"]
    assert len(seen) == 2


@pytest.mark.anyio
async def test_question_is_dropped_after_three_copies() -> None:
    copying = {"question": "Когда рекомендуется проводить электроэнцефалографию при подозрении?"}
    drafter = Drafter(_scripted([copying] * 3, []))

    assert await drafter.single(_fragment("kr-gad", "doc_2", 1), patient=False) is None


@pytest.mark.anyio
async def test_patient_flag_reaches_the_prompt() -> None:
    seen: list[str] = []
    drafter = Drafter(_scripted([{"question": "Можно ли ЭЭГ женщине 34 лет с ГТР?"}], seen))

    await drafter.single(_fragment("kr-gad", "doc_2", 1), patient=True)

    assert "Сведения о пациенте: добавь" in seen[0]


@pytest.mark.anyio
async def test_pair_question_checks_both_fragments() -> None:
    copying_second = {"question": "Как назначают препарат вечером после еды запивая водой?"}
    own = {"question": "Как принимать Авиандр и что делать при пропуске?"}
    drafter = Drafter(_scripted([copying_second, own], []))
    first = _fragment("aviandr-ohlp", "4.2", 1)
    second = _fragment(
        "aviandr-ohlp", "4.2", 2, text="Препарат вечером после еды запивая водой принимают."
    )

    assert await drafter.pair(first, second, patient=False) == own["question"]


@pytest.mark.anyio
async def test_none_questions_are_returned_as_list() -> None:
    answer = {"questions": ["Как лечить СДВГ у детей?", "Сколько стоит курс Авиандра?"]}
    drafter = Drafter(_scripted([answer], []))

    assert await drafter.none(2) == answer["questions"]


@pytest.mark.parametrize("kind", ["single", "multi"])
def test_draft_entry_has_origin_fingerprints_and_text_for_review(kind: str) -> None:
    origin = [_fragment("kr-gad", "doc_3", 1), _fragment("kr-gad", "doc_3", 2)]

    entry = draft_entry(7, kind, "Вопрос?", origin)

    assert entry["id"] == "d-007"
    assert entry["kind"] == kind
    assert entry["origin"] == [{"key": f.key, "fingerprint": fingerprint(f.text)} for f in origin]
    assert entry["source_text"] == f"{TEXT}\n---\n{TEXT}"


def test_draft_entry_for_none_has_no_origin() -> None:
    entry = draft_entry(1, "none", "Вопрос?", [])

    assert entry["origin"] == []
    assert "source_text" not in entry
