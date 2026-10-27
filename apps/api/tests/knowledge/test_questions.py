"""Вопросы к фрагментам при индексации (спека #105, 5.1). LLM — `FunctionModel`."""

from clin_compass.core.ai.llm import Llm
from clin_compass.modules.knowledge.questions import COUNT, TASK, Questioner
from tests.core.fake_nodes import MemoryRecorder
from tests.freetext.helpers import Script

TITLE = "ОХЛП АВИАНДР"
HEADING = "4.3. Противопоказания"
TEXT = "Почечная недостаточность средней и тяжелой степени (СКФ <60 мл/мин)."
ANSWER = {
    "questions": [
        "авиандр при скф 45 можно?",
        "кому нельзя авиандр по почкам",
        "При какой функции почек Авиандр противопоказан?",
        "Ж, 61 год, СКФ 52 мл/мин, тревога полгода. Можно ли назначить Авиандр?",
    ]
}


async def test_questions_are_generated_from_fragment_and_recorded() -> None:
    recorder = MemoryRecorder()
    script = Script(ANSWER)
    questioner = Questioner(Llm(script.model, recorder))

    questions = await questioner.questions(TITLE, HEADING, TEXT)

    assert questions == ANSWER["questions"]
    [call] = script.calls
    assert TITLE in call.prompt and HEADING in call.prompt and TEXT in call.prompt
    items = call.schema["properties"]["questions"]
    assert (items["minItems"], items["maxItems"]) == (COUNT, COUNT)
    [run] = recorder.runs
    assert (run.task, run.prompt_version) == (TASK, questioner.prompt_version)
    assert "СКФ" not in repr(run)


async def test_blank_and_repeated_questions_are_dropped() -> None:
    script = Script({"questions": ["  вопрос  ", "вопрос", " ", "другой"]})

    questions = await Questioner(Llm(script.model, MemoryRecorder())).questions(
        TITLE, HEADING, TEXT
    )

    assert questions == ["вопрос", "другой"]


def test_prompt_version_names_the_task() -> None:
    questioner = Questioner(Llm(Script(ANSWER).model, MemoryRecorder()))

    assert questioner.prompt_version.startswith(f"{TASK}-")
