"""Поддельные узлы инференса для тестов клиентов core/ai: настоящий HTTP на 127.0.0.1.

Узел отвечает функцией-обработчиком и запоминает запросы. Мёртвый узел — закрытый порт.
Обработчик вернул `None` — узел закрывает соединение без ответа.
"""

import asyncio
import inspect
import json
import socket
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest

from clin_compass.core.ai.runs import RunRecord


@dataclass(frozen=True)
class Request:
    """Запрос, который дошёл до узла."""

    method: str
    path: str
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body)


Reply = tuple[int, Any]
Handler = Callable[[Request], Reply | Awaitable[Reply | None] | None]


@dataclass
class FakeNode:
    url: str
    requests: list[Request] = field(default_factory=list)


def health_ok(request: Request) -> Reply:
    """Узел, который отвечает только на GET /health."""
    if request.path == "/health":
        return 200, {"status": "ok"}
    return 404, {"error": "not found"}


@asynccontextmanager
async def fake_node(handler: Handler) -> AsyncIterator[FakeNode]:
    """HTTP-сервер на свободном порту. Ответ — JSON с кодом из обработчика."""
    node = FakeNode(url="")

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = (await reader.readuntil(b"\r\n\r\n")).decode()
        request_line, *header_lines = head.split("\r\n")
        method, path, _ = request_line.split(" ", 2)
        headers = {
            name.lower(): value
            for name, _, value in (line.partition(": ") for line in header_lines if line)
        }
        body = await reader.readexactly(int(headers.get("content-length", "0")))
        request = Request(method=method, path=path, headers=headers, body=body)
        node.requests.append(request)
        reply = handler(request)
        answer = await reply if inspect.isawaitable(reply) else reply
        if answer is None:
            writer.close()
            return
        status, payload = answer
        raw = json.dumps(payload).encode()
        writer.write(
            f"HTTP/1.1 {status} X\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(raw)}\r\nConnection: close\r\n\r\n".encode()
            + raw
        )
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    node.url = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    async with server:
        yield node


def dead_node_url() -> str:
    """Адрес закрытого порта: соединение с ним сразу получает отказ."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"http://127.0.0.1:{port}"


def set_ambient_openai_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Переменные окружения, которые OpenAI SDK сам подставляет в запрос."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-ambient-secret")
    monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", "Authorization: Bearer sk-ambient-header")
    monkeypatch.setenv("OPENAI_ORG_ID", "org-ambient")
    monkeypatch.setenv("OPENAI_PROJECT_ID", "proj-ambient")


def assert_no_ambient_openai_values(request: Request) -> None:
    """Ни ключ, ни заголовки, ни организация из окружения до узла не дошли."""
    assert request.headers["authorization"] == "Bearer not-used"
    assert "openai-organization" not in request.headers
    assert "openai-project" not in request.headers
    assert "ambient" not in repr(request.headers)


class MemoryRecorder:
    """Складывает записи ai_runs в список вместо базы. Запись в базу проверяет test_ai_runs."""

    def __init__(self) -> None:
        self.runs: list[RunRecord] = []

    async def record(self, run: RunRecord) -> UUID:
        self.runs.append(run)
        return uuid4()
