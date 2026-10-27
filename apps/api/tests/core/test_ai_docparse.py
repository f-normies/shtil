"""Клиент docparse: страницы PDF → текст olmOCR 2 на llama-server (спека #27, раздел 9.2).

Узлы перебираются по порядку, как у ASR. Каждый запрос к модели — строка ai_runs.
PDF собирается в тесте; текст страниц — синтетический.
"""

import asyncio
import base64
import ctypes
import hashlib
import io
import traceback
from collections.abc import Callable
from typing import Any

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
import pytest
import structlog
from PIL import Image

from clin_compass.core.ai.docparse import (
    MODEL_ALIAS,
    PROMPT,
    PROMPT_VERSION,
    DocparsePageError,
    ParsedPage,
    ParsedPdf,
    build_docparse_client,
)
from clin_compass.core.ai.nodes import (
    AiNoAnswerError,
    AiNotConfiguredError,
    AiOutputError,
    AiServiceError,
    NodesUnavailableError,
)
from clin_compass.core.config import Settings
from tests.core.fake_nodes import (
    MemoryRecorder,
    Reply,
    Request,
    assert_no_ambient_openai_values,
    dead_node_url,
    fake_node,
    set_ambient_openai_env,
)

# build_no_anchoring_v4_yaml_prompt() из allenai/olmocr (olmocr/prompts/prompts.py, f7cfe4c).
OLMOCR_PROMPT = (
    "Attached is one page of a document that you must process. "
    "Just return the plain text representation of this document as if you were reading it "
    "naturally. Convert equations to LateX and tables to HTML.\n"
    "If there are any figures or charts, label them with the following markdown syntax "
    "![Alt text describing the contents of the figure](page_startx_starty_width_height.png)\n"
    "Return your output as markdown, with a front matter section on top specifying values for "
    "the primary_language, is_rotation_valid, rotation_correction, is_table, and is_diagram "
    "parameters."
)
HEADER = (
    "---\nprimary_language: ru\nis_rotation_valid: True\nrotation_correction: 0\n"
    "is_table: False\nis_diagram: False\n---\n"
)
PORTRAIT = (595.0, 842.0)
LANDSCAPE = (842.0, 595.0)
TASK = "knowledge.parse"


def _pdf(*pages: tuple[float, float, str]) -> bytes:
    """PDF со страницами заданного размера и строкой текста Helvetica на каждой."""
    pdf = pdfium.PdfDocument.new()
    for width, height, text in pages:
        page = pdf.new_page(width, height)
        obj = pdfium_c.FPDFPageObj_NewTextObj(pdf.raw, b"Helvetica", ctypes.c_float(12))
        encoded = (text + "\0").encode("utf-16-le")
        buffer = ctypes.create_string_buffer(encoded, len(encoded))
        pdfium_c.FPDFText_SetText(obj, ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ushort)))
        pdfium_c.FPDFPageObj_Transform(obj, 1, 0, 0, 1, 20, height / 2)
        pdfium_c.FPDFPage_InsertObject(page.raw, obj)
        page.gen_content()
        page.close()
    out = io.BytesIO()
    pdf.save(out)
    pdf.close()
    return out.getvalue()


def _reply(content: str | None, finish_reason: str = "stop") -> Reply:
    """Ответ llama-server на /v1/chat/completions."""
    message = {"role": "assistant", "content": content}
    return 200, {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": MODEL_ALIAS,
        "choices": [{"index": 0, "finish_reason": finish_reason, "message": message}],
        "usage": {"prompt_tokens": 1700, "completion_tokens": 40, "total_tokens": 1740},
    }


def _script(*replies: Reply) -> Callable[[Request], Reply]:
    """Узел, который отвечает заготовленными ответами по порядку."""
    queue = list(replies)
    return lambda request: queue.pop(0)


def _png(request: Request) -> bytes:
    """Картинка страницы из запроса: data-URL второй части сообщения."""
    url = request.json()["messages"][0]["content"][1]["image_url"]["url"]
    prefix = "data:image/png;base64,"
    assert url.startswith(prefix)
    return base64.b64decode(url.removeprefix(prefix))


def _image_size(request: Request) -> tuple[int, int]:
    with Image.open(io.BytesIO(_png(request))) as image:
        assert image.format == "PNG"
        return image.size


def _settings(*urls: str, **overrides: Any) -> Settings:
    # Значения передаём явно: Makefile экспортирует .env, где может стоять настоящий адрес.
    return Settings(
        database_url="postgresql+asyncpg://unused/none",
        environment="test",
        docparse_urls=urls,
        **overrides,
    )


async def _parse(settings: Settings, recorder: MemoryRecorder, pdf: bytes) -> ParsedPdf:
    return await build_docparse_client(settings, recorder).parse_pdf(pdf, task=TASK)


ONE_PAGE = _pdf((*PORTRAIT, "Layer text"))


async def test_pages_come_in_order_with_model_text_and_text_layer() -> None:
    pdf = _pdf((*PORTRAIT, "First page layer"), (*LANDSCAPE, "Second page layer"))
    replies = _script(_reply(HEADER + "Текст первой"), _reply(HEADER + "Текст второй"))

    async with fake_node(replies) as node:
        parsed = await _parse(_settings(node.url), MemoryRecorder(), pdf)

    assert parsed.pages == (
        ParsedPage(number=1, text="Текст первой", layer_text="First page layer"),
        ParsedPage(number=2, text="Текст второй", layer_text="Second page layer"),
    )
    # Страницы ушли по одной и по порядку: первая книжная, вторая альбомная.
    assert [_image_size(request) for request in node.requests] == [(911, 1288), (1288, 911)]


async def test_request_is_prompt_then_png_page_without_system_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_ambient_openai_env(monkeypatch)

    async with fake_node(_script(_reply(HEADER + "Текст"))) as node:
        await _parse(_settings(node.url), MemoryRecorder(), ONE_PAGE)

    [request] = node.requests
    assert (request.method, request.path) == ("POST", "/v1/chat/completions")
    assert_no_ambient_openai_values(request)
    body = request.json()
    [message] = body["messages"]
    assert message["role"] == "user"
    text, image = message["content"]
    assert text == {"type": "text", "text": OLMOCR_PROMPT}
    assert image["type"] == "image_url"
    assert max(_image_size(request)) == 1288
    assert body["model"] == "olmocr-2"
    assert (body["max_tokens"], body["temperature"], body["repeat_penalty"]) == (8000, 0.1, 1.05)


async def test_longest_side_stays_1288_when_scale_rounds_up() -> None:
    # 300.33 × (1288 / 300.33) в числах с плавающей точкой чуть больше 1288,
    # а pypdfium2 округляет размер картинки вверх.
    async with fake_node(_script(_reply(HEADER))) as node:
        await _parse(_settings(node.url), MemoryRecorder(), _pdf((300.33, 100.0, "x")))

    assert _image_size(node.requests[0])[0] == 1288


async def test_front_matter_is_cut_from_page_text() -> None:
    answer = HEADER + "4.3. Противопоказания\n\nГиперчувствительность.\n\n"

    async with fake_node(_script(_reply(answer), _reply(HEADER))) as node:
        parsed = await _parse(
            _settings(node.url), MemoryRecorder(), _pdf((*PORTRAIT, "a"), (*PORTRAIT, "b"))
        )

    assert parsed.pages[0].text == "4.3. Противопоказания\n\nГиперчувствительность."
    # Пустая страница: шапка без текста.
    assert parsed.pages[1].text == ""


BAD_ANSWERS = {
    "length": _reply(HEADER + "Текст оборван", finish_reason="length"),
    "no_header": _reply("Текст без шапки"),
    "unclosed_header": _reply("---\nprimary_language: ru\nis_rotation_valid: True\nТекст"),
    "no_rotation_field": _reply("---\nprimary_language: ru\n---\nТекст"),
    "rotation_not_bool": _reply("---\nis_rotation_valid: maybe\n---\nТекст"),
    "line_without_colon": _reply("---\nis_rotation_valid: True\nмусор\n---\nТекст"),
    "no_content": _reply(None),
}


@pytest.mark.parametrize("bad", BAD_ANSWERS.values(), ids=BAD_ANSWERS.keys())
async def test_bad_answer_is_retried_with_rising_temperature(bad: Reply) -> None:
    async with fake_node(_script(bad, bad, _reply(HEADER + "Текст"))) as node:
        parsed = await _parse(_settings(node.url), MemoryRecorder(), ONE_PAGE)

    assert parsed.pages[0].text == "Текст"
    assert [request.json()["temperature"] for request in node.requests] == [0.1, 0.1, 0.2]


async def test_three_bad_answers_fail_the_page_without_its_text() -> None:
    recorder = MemoryRecorder()
    bad = _reply(HEADER + "Секретный текст страницы", finish_reason="length")
    pdf = _pdf((*PORTRAIT, "a"), (*PORTRAIT, "b"))

    async with fake_node(_script(_reply(HEADER + "Первая"), bad, bad, bad)) as node:
        with pytest.raises(AiOutputError) as caught:
            await _parse(_settings(node.url), recorder, pdf)

    error = caught.value
    assert isinstance(error, DocparsePageError)
    assert (error.role, error.page) == ("docparse", 2)
    assert "страница 2" in str(error)
    assert "Секретный" not in "".join(traceback.format_exception(error))
    assert len(node.requests) == 4
    assert [run.status for run in recorder.runs] == ["ok", "error", "error", "error"]


async def test_rotated_page_fails_without_retry() -> None:
    rotated = HEADER.replace("is_rotation_valid: True", "is_rotation_valid: False")
    recorder = MemoryRecorder()

    async with fake_node(_script(_reply(rotated + "Текст"))) as node:
        with pytest.raises(DocparsePageError, match="страница 1") as caught:
            await _parse(_settings(node.url), recorder, ONE_PAGE)

    assert caught.value.page == 1
    assert len(node.requests) == 1
    assert recorder.runs[0].error == "DocparsePageError"


async def test_each_model_call_is_recorded_with_page_and_png_hash() -> None:
    recorder = MemoryRecorder()
    replies = _script(
        _reply(HEADER + "Первая"), _reply("Текст без шапки"), _reply(HEADER + "Вторая")
    )
    pdf = _pdf((*PORTRAIT, "a"), (*LANDSCAPE, "b"))

    async with fake_node(replies) as node:
        await _parse(_settings(node.url), recorder, pdf)

    runs = recorder.runs
    assert {(run.role, run.task, run.model, run.node) for run in runs} == {
        ("docparse", TASK, "olmocr-2", node.url)
    }
    assert [run.params for run in runs] == [
        {"page": 1, "attempt": 1, "temperature": 0.1},
        {"page": 2, "attempt": 1, "temperature": 0.1},
        {"page": 2, "attempt": 2, "temperature": 0.1},
    ]
    assert [(run.status, run.error) for run in runs] == [
        ("ok", None),
        ("error", "AiOutputError"),
        ("ok", None),
    ]
    assert [run.input_hash for run in runs] == [
        hashlib.sha256(_png(request)).hexdigest() for request in node.requests
    ]
    assert (runs[0].input_tokens, runs[0].output_tokens) == (1700, 40)
    # Версия промпта olmOCR — 8 знаков SHA-256 его текста: правка промпта меняет её сама.
    assert f"olmocr-{hashlib.sha256(PROMPT.encode()).hexdigest()[:8]}" == PROMPT_VERSION
    assert {run.prompt_version for run in runs} == {PROMPT_VERSION}
    assert "Первая" not in repr(runs)
    assert "Текст" not in repr(runs)


@pytest.mark.parametrize("first", ["dead", "not_ready"])
async def test_page_goes_to_next_node_when_first_is_unavailable(first: str) -> None:
    recorder = MemoryRecorder()

    async with (
        fake_node(lambda request: (503, {"error": {"message": "Loading model"}})) as not_ready,
        fake_node(_script(_reply(HEADER + "Текст"))) as live,
    ):
        first_url = dead_node_url() if first == "dead" else not_ready.url
        with structlog.testing.capture_logs() as logs:
            parsed = await _parse(_settings(first_url, live.url), recorder, ONE_PAGE)

    assert parsed.pages[0].text == "Текст"
    assert recorder.runs[0].node == live.url
    assert logs == [
        {
            "event": "ai.node_unavailable",
            "log_level": "warning",
            "role": "docparse",
            "node": first_url,
            "error": "APIConnectionError" if first == "dead" else "InternalServerError",
        }
    ]


async def test_all_nodes_down_raises_nodes_unavailable_and_is_recorded() -> None:
    recorder = MemoryRecorder()

    with pytest.raises(NodesUnavailableError):
        await _parse(_settings(dead_node_url(), dead_node_url()), recorder, ONE_PAGE)

    [run] = recorder.runs
    assert (run.status, run.error, run.node) == ("error", "NodesUnavailableError", None)


async def test_server_error_is_not_sent_to_next_node() -> None:
    async with (
        fake_node(lambda request: (500, {"error": {"message": "Internal"}})) as broken,
        fake_node(_script(_reply(HEADER))) as spare,
    ):
        with pytest.raises(AiServiceError) as caught:
            await _parse(_settings(broken.url, spare.url), MemoryRecorder(), ONE_PAGE)

    assert caught.value.status == 500
    # В цепочке причин был бы ответ узла с телом.
    assert caught.value.__cause__ is None
    assert spare.requests == []


async def _hang(request: Request) -> Reply:
    await asyncio.sleep(1)
    return _reply(HEADER)


@pytest.mark.parametrize("handler", [_hang, lambda request: None], ids=["timeout", "dropped"])
async def test_no_answer_is_not_sent_to_next_node(handler: Any) -> None:
    # Запрос дошёл до модели: повтор на другом узле удвоил бы нагрузку.
    recorder = MemoryRecorder()

    async with fake_node(handler) as silent, fake_node(_script(_reply(HEADER))) as spare:
        settings = _settings(silent.url, spare.url, docparse_timeout_seconds=0.2)
        with pytest.raises(AiNoAnswerError) as caught:
            await _parse(settings, recorder, ONE_PAGE)

    assert spare.requests == []
    assert caught.value.__cause__ is None
    assert recorder.runs[0].error == "AiNoAnswerError"


def test_build_docparse_client_requires_node_urls() -> None:
    with pytest.raises(AiNotConfiguredError, match="COMPASS_DOCPARSE_URLS"):
        build_docparse_client(_settings(), MemoryRecorder())
