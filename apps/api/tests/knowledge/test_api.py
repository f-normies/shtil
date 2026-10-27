"""HTTP API базы знаний (спека #27, раздел 8): только врач; 503 и 502 при сбое модели; 404."""

from collections.abc import AsyncIterator, Sequence
from uuid import uuid4

import httpx
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from pydantic_ai.models.function import FunctionModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.core.ai.llm import Llm
from clin_compass.core.config import Settings
from clin_compass.main import create_app
from clin_compass.modules.identity.schemas import Role
from clin_compass.modules.knowledge import deps, service
from tests.conftest import MakeUser
from tests.core.fake_nodes import MemoryRecorder, Reply, Request, dead_node_url, fake_node
from tests.freetext.helpers import Script
from tests.knowledge.helpers import (
    Piece,
    add_document,
    probe_model,
    words_embedder,
    words_vector,
)
from tests.live import PASSWORD

ANSWERS = "/api/v1/knowledge/answers"
QUESTION = {"question": "Противопоказания маритупирдина при беременности?"}
PIECE = Piece(
    "Противопоказания: беременность и период грудного вскармливания.",
    section="4.3",
    section_title="Противопоказания",
    heading_path="4.3. Противопоказания",
)


async def login(client: httpx.AsyncClient, make_user: MakeUser, role: Role = Role.DOCTOR) -> None:
    email = f"{role.value}@example.com"
    await make_user(email=email, role=role)
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200


def use_ai(app: FastAPI, script: Script | FunctionModel) -> None:
    embedder, _ = words_embedder()
    model = script.model if isinstance(script, Script) else script
    ai = service.KnowledgeAi(embedder, Llm(model, MemoryRecorder()))
    app.dependency_overrides[deps.get_ai] = lambda: ai


@pytest.fixture
async def ohlp(sessionmaker: async_sessionmaker[AsyncSession]) -> str:
    async with sessionmaker() as session:
        _, [fragment] = await add_document(
            session, "aviandr-ohlp", [PIECE], kind="drug_label", title="ОХЛП", version="0010"
        )
        await session.commit()
    return str(fragment.id)


async def test_doctor_gets_answer_with_checked_quotes(
    app: FastAPI, client: httpx.AsyncClient, make_user: MakeUser, ohlp: str
) -> None:
    claims = [
        {"statement": "Нельзя.", "fragment": "F1", "first": 1, "last": 1},
        {"statement": "Выдумка.", "fragment": "F1", "first": 2, "last": 2},
    ]
    use_ai(app, Script({"claims": claims}))
    await login(client, make_user)

    response = await client.post(ANSWERS, json={**QUESTION, "source_ids": ["aviandr-ohlp"]})

    assert response.status_code == 200
    body = response.json()
    assert body["dropped"] == 1
    [claim] = body["claims"]
    assert claim["quote"] == PIECE.text
    assert claim["fragment"]["status"] == "active"
    assert claim["fragment"]["id"] == ohlp
    assert claim["fragment"]["section"] == "4.3"


async def test_model_without_claims_gives_empty_answer(
    app: FastAPI, client: httpx.AsyncClient, make_user: MakeUser, ohlp: str
) -> None:
    # Пустой ответ кабинет врача показывает как «В базе знаний ответа нет» (спека #108, 3.3).
    script = Script({"claims": []})
    use_ai(app, script)
    await login(client, make_user)

    response = await client.post(ANSWERS, json=QUESTION)

    assert response.status_code == 200
    body = response.json()
    assert (body["claims"], body["dropped"]) == ([], 0)
    # Модель вызывалась: `run_id` — номер записи вызова, в тесте её хранит MemoryRecorder.
    assert len(script.calls) == 1
    assert body["run_id"] is not None


async def test_model_is_called_with_no_database_connection_held(
    app: FastAPI, client: httpx.AsyncClient, make_user: MakeUser, ohlp: str
) -> None:
    # Ответ модели идёт до минуты: всё это время соединение с БД не должно висеть
    # в открытой транзакции, ни от проверки входа, ни от поиска.
    held: list[int] = []
    use_ai(
        app, probe_model({"claims": []}, lambda: held.append(app.state.engine.pool.checkedout()))
    )
    await login(client, make_user)

    response = await client.post(ANSWERS, json=QUESTION)

    assert response.status_code == 200
    assert held == [0]


async def test_reranker_is_called_with_no_database_connection_held(
    app: FastAPI, client: httpx.AsyncClient, make_user: MakeUser, ohlp: str
) -> None:
    # Ранжировщик на сбойном узле ждёт до rerank_timeout_seconds: соединение с БД
    # всё это время должно быть в пуле.
    held: list[int] = []

    class ProbeReranker:
        async def rerank(self, query: str, documents: Sequence[str], *, task: str) -> list[float]:
            held.append(app.state.engine.pool.checkedout())
            return [0.0] * len(documents)

    embedder, _ = words_embedder()
    ai = service.KnowledgeAi(
        embedder, Llm(Script({"claims": []}).model, MemoryRecorder()), reranker=ProbeReranker()
    )
    app.dependency_overrides[deps.get_ai] = lambda: ai
    await login(client, make_user)

    response = await client.post(ANSWERS, json=QUESTION)

    assert response.status_code == 200
    assert held == [0]


@pytest.mark.parametrize("role", [Role.PATIENT, Role.ADMIN])
async def test_only_doctor_asks(
    app: FastAPI, client: httpx.AsyncClient, make_user: MakeUser, role: Role, ohlp: str
) -> None:
    use_ai(app, Script({"claims": []}))
    await login(client, make_user, role)

    assert (await client.post(ANSWERS, json=QUESTION)).status_code == 403
    assert (await client.get(f"/api/v1/knowledge/fragments/{ohlp}")).status_code == 403


async def test_anonymous_gets_401(client: httpx.AsyncClient) -> None:
    assert (await client.post(ANSWERS, json=QUESTION)).status_code == 401


@pytest.mark.parametrize(
    "body",
    [
        {"question": ""},
        {"question": " \n\t "},
        {"question": "в" * 1001},
        {**QUESTION, "source_ids": []},
    ],
)
async def test_question_is_validated(
    app: FastAPI, client: httpx.AsyncClient, make_user: MakeUser, body: dict[str, object]
) -> None:
    use_ai(app, Script({"claims": []}))
    await login(client, make_user)

    assert (await client.post(ANSWERS, json=body)).status_code == 422


async def test_question_is_trimmed(
    app: FastAPI, client: httpx.AsyncClient, make_user: MakeUser, ohlp: str
) -> None:
    script = Script({"claims": []})
    use_ai(app, script)
    await login(client, make_user)

    response = await client.post(ANSWERS, json={"question": "  Противопоказания?\n"})

    assert response.status_code == 200
    [call] = script.calls
    assert "<вопрос>\nПротивопоказания?\n</вопрос>" in call.prompt


async def test_model_error_gives_502(
    app: FastAPI, client: httpx.AsyncClient, make_user: MakeUser, ohlp: str
) -> None:
    use_ai(app, Script({"claims": [{"statement": "?", "fragment": "F9", "first": 1, "last": 1}]}))
    await login(client, make_user)

    response = await client.post(ANSWERS, json=QUESTION)

    assert response.status_code == 502


async def test_unconfigured_models_give_503(client: httpx.AsyncClient, make_user: MakeUser) -> None:
    # Фикстура settings не задаёт узлов: клиенты моделей не собраны.
    await login(client, make_user)

    response = await client.post(ANSWERS, json=QUESTION)

    assert response.status_code == 503
    assert response.json()["detail"] == "Модель недоступна, повторите позже"


def _embeddings(request: Request) -> Reply:
    body = request.json()
    data = [
        {"object": "embedding", "index": i, "embedding": words_vector(t)}
        for i, t in enumerate(body["input"])
    ]
    usage = {"prompt_tokens": 7, "total_tokens": 7}
    return 200, {"object": "list", "model": body["model"], "data": data, "usage": usage}


@pytest.fixture
async def wired(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    """Приложение с настоящими клиентами: узел эмбеддингов отвечает, узел LLM выключен."""
    async with fake_node(_embeddings) as node:
        configured = settings.model_copy(
            update={"embeddings_urls": (node.url,), "llm_urls": (dead_node_url(),)}
        )
        app = create_app(configured)
        async with (
            LifespanManager(app) as manager,
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=manager.app), base_url="http://test"
            ) as client,
        ):
            yield client


async def test_unavailable_llm_gives_503_and_runs_are_recorded(
    wired: httpx.AsyncClient,
    make_user: MakeUser,
    sessionmaker: async_sessionmaker[AsyncSession],
    ohlp: str,
) -> None:
    await login(wired, make_user)

    response = await wired.post(ANSWERS, json=QUESTION)

    assert response.status_code == 503
    async with sessionmaker() as session:
        runs = (
            await session.execute(
                text("SELECT role, task, status FROM ai.ai_runs ORDER BY created_at")
            )
        ).all()
    assert [tuple(r) for r in runs] == [
        ("embeddings", "knowledge.search", "ok"),
        ("llm", "knowledge.answer", "error"),
    ]


async def test_doctor_opens_fragment(
    client: httpx.AsyncClient, make_user: MakeUser, ohlp: str
) -> None:
    await login(client, make_user)

    response = await client.get(f"/api/v1/knowledge/fragments/{ohlp}")

    assert response.status_code == 200
    body = response.json()
    assert body["text"] == PIECE.text
    assert (body["section"], body["section_title"]) == ("4.3", "Противопоказания")
    document = body["document"]
    assert (document["title"], document["version"]) == ("ОХЛП", "0010")
    assert document["url"] == "https://example.org/aviandr-ohlp"
    assert document["status"] == "active"


async def test_unknown_fragment_gives_404(client: httpx.AsyncClient, make_user: MakeUser) -> None:
    await login(client, make_user)

    response = await client.get(f"/api/v1/knowledge/fragments/{uuid4()}")

    assert response.status_code == 404
