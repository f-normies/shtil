"""Команда `cli index` (спека #27, раздел 5): строка на источник и код возврата.

Настоящие клиенты на поддельных узлах: API рубрикатора и эмбеддинги отвечают с 127.0.0.1.
"""

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass import cli
from clin_compass.core.config import Settings
from tests.core.fake_nodes import Reply, Request, fake_node
from tests.knowledge.helpers import words_vector

MANIFEST = """sources:
  - id: kr-test
    title: "Тестовая рекомендация"
    kind: clinical_guideline
    publisher: "Издатель"
    url: https://example.org/kr-test
    version: "900_1"
    date: 2026-01-01
    license: "синтетика для тестов"
    file: null
    supplementary: false
    icd10: [F41.1]
  - id: other-ohlp
    title: "Инструкция без файла"
    kind: drug_label
    publisher: "Издатель"
    url: https://example.org/other-ohlp
    version: "0001"
    date: 2026-01-01
    license: "синтетика для тестов"
    file: null
    supplementary: false
"""
# Один пункт со строкой уровней: рекомендация без пунктов — ошибка индексации.
TREATMENT = (
    "<ul><li><p>Пациентам рекомендуется сон.</p></li></ul><p><strong>Уровень убедительности"
    " рекомендаций – A (уровень достоверности доказательств – 1)</strong></p>"
)
GUIDELINE = {
    "id": "900_1",
    "obj": {"sections": [{"id": "doc_3", "title": "3. Лечение", "content": TREATMENT}]},
}


def _node(request: Request) -> Reply:
    """API рубрикатора и llama-server с эмбеддингами на одном поддельном узле."""
    if request.path.startswith("/api.ashx"):
        return 200, GUIDELINE
    body = request.json()
    data = [
        {"object": "embedding", "index": i, "embedding": words_vector(t)}
        for i, t in enumerate(body["input"])
    ]
    usage = {"prompt_tokens": 7, "total_tokens": 7}
    return 200, {"object": "list", "model": body["model"], "data": data, "usage": usage}


@pytest.fixture
def knowledge_dir(tmp_path: Path) -> Path:
    (tmp_path / "sources").mkdir()
    (tmp_path / "sources" / "manifest.yaml").write_text(MANIFEST, encoding="utf-8")
    return tmp_path


async def test_index_prints_line_per_source_and_fails_on_error(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    knowledge_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async with fake_node(_node) as node:
        configured = settings.model_copy(
            update={
                "knowledge_dir": knowledge_dir,
                "knowledge_guidelines_api_url": f"{node.url}/api.ashx",
                "embeddings_urls": (node.url,),
                "docparse_urls": (),
            }
        )
        code = await cli.index(configured, force=False)

    assert code == 1
    assert capsys.readouterr().out.splitlines() == [
        "kr-test: проиндексирован, фрагментов: 1",
        "other-ohlp: ошибка: у инструкции нет файла PDF в манифесте (file)",
    ]
    assert node.requests[0].path == "/api.ashx?op=GetClinrec2&id=900_1"
    async with sessionmaker() as session:
        tasks = list(await session.scalars(text("SELECT task FROM ai.ai_runs")))
    assert tasks == ["knowledge.index"]


async def test_index_without_embedding_nodes_fails(
    settings: Settings, knowledge_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    configured = settings.model_copy(update={"knowledge_dir": knowledge_dir, "embeddings_urls": ()})

    code = await cli.index(configured, force=True)

    assert code == 1
    assert "COMPASS_EMBEDDINGS_URLS" in capsys.readouterr().out


def test_guidelines_api_url_defaults_to_rubricator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COMPASS_KNOWLEDGE_GUIDELINES_API_URL", raising=False)

    settings = Settings(database_url="postgresql+asyncpg://unused/none")

    assert settings.knowledge_guidelines_api_url == "https://apicr.minzdrav.gov.ru/api.ashx"
