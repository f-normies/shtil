"""Признак голосовой записи в `evidence` сигнала и в `payload` находки (спека #13, раздел 5.4).

Кабинет врача подписывает цитату «голосовая запись»: `source_type` различает только
дневник и чат. Функции чистые, база не нужна.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from clin_compass_knowledge.loader import Knowledge
from clin_compass_knowledge.schemas.source import Source

from clin_compass.modules.freetext.analysis import FoundSign, TextFinding
from clin_compass.modules.freetext.models import Analysis
from clin_compass.modules.freetext.signals import _evidence
from clin_compass.modules.freetext.tasks import finding_payload


def sources(knowledge: Knowledge) -> dict[str, Source]:
    return {s.id: s for s in knowledge.manifest}


CREATED = datetime(2026, 10, 19, 8, 30, 15, 123456, tzinfo=UTC)


def analysis(*, transcript: bool) -> Analysis:
    return Analysis(
        id=uuid4(),
        patient_id=uuid4(),
        source_type="diary",
        source_id=uuid4(),
        transcript=transcript,
        text="не хочу жить",
        created_at=CREATED,
    )


def finding() -> TextFinding:
    return TextFinding(
        category="symptom",
        summary="плохой сон",
        quote="плохо сплю",
        drug=None,
        adherence_event=None,
    )


@pytest.mark.parametrize("transcript", [True, False])
def test_signal_evidence_tells_whether_text_came_from_voice(
    knowledge: Knowledge, transcript: bool
) -> None:
    sign = knowledge.signs["test-thoughts"]
    found = [FoundSign(kind=sign.id, quote="не хочу жить", quote_found=True)]

    evidence = _evidence(
        knowledge.signs,
        sources(knowledge),
        analysis(transcript=transcript),
        found,
        sign,
        "check_risk",
    )

    assert evidence["transcript"] is transcript


def test_signal_evidence_names_documents_of_the_sign(knowledge: Knowledge) -> None:
    # Кабинет показывает у AI-сигнала название документа и ссылку, как у сигнала по правилу.
    sign = knowledge.signs["test-thoughts"]
    found = [FoundSign(kind=sign.id, quote="не хочу жить", quote_found=True)]

    evidence = _evidence(
        knowledge.signs, sources(knowledge), analysis(transcript=False), found, sign, "extract"
    )

    assert evidence["sources"] == {
        "test-thoughts": [
            {
                "source": "test-guideline",
                "locator": "раздел 2",
                "title": "Тестовые клинические рекомендации",
                "url": "https://example.org/guideline",
            }
        ]
    }


@pytest.mark.parametrize("transcript", [True, False])
def test_finding_payload_tells_whether_text_came_from_voice(transcript: bool) -> None:
    payload = finding_payload(analysis(transcript=transcript), finding())

    assert payload["transcript"] is transcript


def test_finding_payload_takes_time_of_writing_given_at_submit() -> None:
    # Спека #96, раздел 3.3: голосовое сообщение ставится на разбор после расшифровки,
    # поэтому время записи передаёт вызывающий.
    voice = analysis(transcript=True)
    voice.recorded_at = datetime(2026, 10, 19, 8, 29, 50, tzinfo=UTC)

    payload = finding_payload(voice, finding())

    assert payload["recorded_at"] == "2026-10-19T08:29:50+00:00"


def test_finding_payload_without_time_of_writing_takes_time_of_analysis() -> None:
    # Разбор создан в одной транзакции с записью дневника или сообщением: его время и есть
    # время записи.
    payload = finding_payload(analysis(transcript=False), finding())

    assert payload["recorded_at"] == "2026-10-19T08:30:15.123456+00:00"
