"""Клиент docparse: страницы PDF → текст olmOCR 2 на llama-server (спека #27, раздел 9.2).

Клиент рисует каждую страницу в PNG и отправляет её в `/v1/chat/completions` вместе с промптом
olmOCR. Параметры — как в пайплайне olmOCR (`olmocr/pipeline.py`). Страницы идут по одной:
сервер запущен с `-np 1`. Рядом с текстом модели клиент отдаёт текстовый слой страницы:
по нему вызывающий код сверяет дословность.

Каждый запрос к модели — строка ai_runs, неудачная попытка тоже. Текст страницы — данные
документа: в ai_runs и в ошибки он не попадает, вход — хеш PNG страницы.
"""

import base64
import hashlib
import io
import math
from collections.abc import Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path

import httpx2
import pypdfium2 as pdfium
import structlog
from openai import APIConnectionError, APIStatusError, AsyncOpenAI
from openai.types.chat import ChatCompletion, ChatCompletionUserMessageParam

from clin_compass.core.ai.nodes import (
    AiNoAnswerError,
    AiOutputError,
    AiServiceError,
    NodesUnavailableError,
    is_unavailable,
    node_urls,
    openai_client,
    report_unavailable,
)
from clin_compass.core.ai.runs import RunRecorder, hash_input, track_run
from clin_compass.core.config import Settings

log = structlog.get_logger()

# Имя модели в llama-server: --alias в services/docparse/serve.sh (MODEL_ALIAS в versions.env).
MODEL_ALIAS = "olmocr-2"
# Дословно build_no_anchoring_v4_yaml_prompt() из github.com/allenai/olmocr,
# olmocr/prompts/prompts.py, коммит f7cfe4c22098b154c76b6ec950d1c0a464eecf8d.
PROMPT = (Path(__file__).parent / "prompts" / "olmocr.md").read_text(encoding="utf-8")
PROMPT = PROMPT.removesuffix("\n")
# Версия промпта в ai_runs: 8 знаков SHA-256 текста, как у промптов задач.
PROMPT_VERSION = f"olmocr-{hashlib.sha256(PROMPT.encode()).hexdigest()[:8]}"
# Длинная сторона картинки страницы, как --target_longest_image_dim в пайплайне olmOCR.
LONGEST_SIDE = 1288
_MAX_TOKENS = 8000
# vLLM берёт repeat_penalty из generation_config.json модели, llama-server — только из запроса.
_REPEAT_PENALTY = 1.05
# Температура по попыткам: начало TEMPERATURE_BY_ATTEMPT пайплайна olmOCR.
_TEMPERATURES = (0.1, 0.1, 0.2)


@dataclass(frozen=True)
class ParsedPage:
    """Страница PDF: номер с 1, текст модели без YAML-шапки и текстовый слой из PDF."""

    number: int
    text: str
    # Пустая строка — у страницы нет текстового слоя (скан).
    layer_text: str


@dataclass(frozen=True)
class ParsedPdf:
    """Страницы PDF по порядку."""

    pages: tuple[ParsedPage, ...]


class DocparsePageError(AiOutputError):
    """Страницу не удалось разобрать. В тексте ошибки — номер страницы и причина, без текста."""

    def __init__(self, page: int, reason: str) -> None:
        super().__init__("docparse")
        self.args = (f"docparse, страница {page}: {reason}.",)
        self.page = page


class DocparseClient:
    """Разбор PDF на узлах docparse по порядку, с записью каждого запроса в ai_runs."""

    def __init__(
        self, urls: Sequence[str], recorder: RunRecorder, *, timeout: httpx2.Timeout
    ) -> None:
        self._urls = tuple(urls)
        self._recorder = recorder
        self._timeout = timeout

    async def parse_pdf(self, pdf: bytes, *, task: str) -> ParsedPdf:
        """Разбирает PDF постранично.

        Страница рисуется в PNG тут же, в цикле событий: это доли секунды против
        десятков секунд работы модели.

        Args:
            pdf: Байты PDF.
            task: Имя задачи для ai_runs, например `knowledge.parse`.

        Raises:
            DocparsePageError: Модель трижды ответила не по формату olmOCR или считает
                страницу повёрнутой. Подкласс `AiOutputError`.
            AiServiceError: Узел ответил ошибкой HTTP.
            NodesUnavailableError: Ни один узел docparse не принял запрос.
            AiNoAnswerError: Узел принял запрос, но не ответил: таймаут ответа или обрыв.
            pypdfium2.PdfiumError: PDF не открывается.
        """
        pages: list[ParsedPage] = []
        async with AsyncExitStack() as stack:
            clients = [
                (url, await stack.enter_async_context(openai_client(url, self._timeout)))
                for url in self._urls
            ]
            document = pdfium.PdfDocument(pdf)
            try:
                for index in range(len(document)):
                    png, layer_text = _read_page(document, index)
                    text = await self._parse_page(clients, png, number=index + 1, task=task)
                    pages.append(ParsedPage(number=index + 1, text=text, layer_text=layer_text))
            finally:
                document.close()
        return ParsedPdf(pages=tuple(pages))

    async def _parse_page(
        self, clients: Sequence[tuple[str, AsyncOpenAI]], png: bytes, *, number: int, task: str
    ) -> str:
        """Текст страницы: до трёх попыток с ростом температуры."""
        for attempt, temperature in enumerate(_TEMPERATURES, start=1):
            text = await self._attempt(clients, png, number, attempt, temperature, task)
            if text is not None:
                return text
            log.warning("docparse.page_retry", page=number, attempt=attempt)
        raise DocparsePageError(number, f"{len(_TEMPERATURES)} ответа не по формату olmOCR")

    async def _attempt(
        self,
        clients: Sequence[tuple[str, AsyncOpenAI]],
        png: bytes,
        number: int,
        attempt: int,
        temperature: float,
        task: str,
    ) -> str | None:
        """Один запрос к модели. `None` — ответ не по формату, попытку стоит повторить."""
        try:
            async with track_run(
                self._recorder,
                role="docparse",
                task=task,
                model=MODEL_ALIAS,
                input_hash=hash_input(png),
                params={"page": number, "attempt": attempt, "temperature": temperature},
                prompt_version=PROMPT_VERSION,
            ) as run:
                run.node, completion = await _complete(clients, png, temperature)
                if completion.usage is not None:
                    run.input_tokens = completion.usage.prompt_tokens
                    run.output_tokens = completion.usage.completion_tokens
                text = _page_text(completion, number)
        except DocparsePageError:
            raise
        except AiOutputError:
            # Строка ai_runs с ошибкой уже записана.
            return None
        return text


async def _complete(
    clients: Sequence[tuple[str, AsyncOpenAI]], png: bytes, temperature: float
) -> tuple[str, ChatCompletion]:
    """Запрос страницы к узлам по порядку. Возвращает адрес узла, который ответил, и ответ."""
    image_url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    # Одно сообщение пользователя: промпт, затем картинка. Системного промпта нет.
    message: ChatCompletionUserMessageParam = {
        "role": "user",
        "content": [
            {"type": "text", "text": PROMPT},
            {"type": "image_url", "image_url": {"url": image_url}},
        ],
    }
    for url, client in clients:
        try:
            completion = await client.chat.completions.create(
                model=MODEL_ALIAS,
                messages=[message],
                max_tokens=_MAX_TOKENS,
                temperature=temperature,
                extra_body={"repeat_penalty": _REPEAT_PENALTY},
            )
        except APIStatusError as exc:
            if _status_unavailable(exc):
                report_unavailable("docparse", url, exc)
                continue
            # from None: в цепочке был бы ответ узла с телом.
            raise AiServiceError("docparse", exc.status_code) from None
        except APIConnectionError as exc:
            if is_unavailable(exc):
                report_unavailable("docparse", url, exc)
                continue
            # Таймаут ответа или обрыв: запрос мог дойти до модели.
            raise AiNoAnswerError("docparse") from None
        return url, completion
    raise NodesUnavailableError("docparse")


def _status_unavailable(exc: APIStatusError) -> bool:
    """`is_unavailable` для ответа с ошибкой. SDK бросает его без причины (`from None`),
    поэтому правило проверяем на ошибке httpx2 с тем же ответом.
    """
    error = httpx2.HTTPStatusError("", request=exc.request, response=exc.response)
    return is_unavailable(error)


def _page_text(completion: ChatCompletion, number: int) -> str:
    """Текст страницы из ответа olmOCR без YAML-шапки.

    Raises:
        AiOutputError: Ответ оборван или шапка не разобралась: попытку стоит повторить.
        DocparsePageError: Модель считает страницу повёрнутой. Наши PDF не повёрнуты,
            поворот страниц клиент не делает.
    """
    choice = completion.choices[0] if completion.choices else None
    if choice is None or choice.finish_reason != "stop" or choice.message.content is None:
        raise AiOutputError("docparse")
    parsed = _split_front_matter(choice.message.content)
    if parsed is None:
        raise AiOutputError("docparse")
    header, text = parsed
    rotation_valid = header.get("is_rotation_valid", "").lower()
    if rotation_valid not in {"true", "false"}:
        raise AiOutputError("docparse")
    if rotation_valid == "false":
        raise DocparsePageError(number, "модель считает страницу повёрнутой")
    return text


def _split_front_matter(answer: str) -> tuple[dict[str, str], str] | None:
    """Шапка `key: value` между строками `---` и текст после неё, как в olmOCR.

    Нужен один булев ключ, поэтому YAML разбирается построчно, без pyyaml.
    `None` — шапки нет, она не закрыта или в ней строка без двоеточия.
    """
    if not answer.startswith("---\n"):
        return None
    end = answer.find("\n---", 4)
    if end == -1:
        return None
    header: dict[str, str] = {}
    for line in answer[4:end].splitlines():
        key, colon, value = line.partition(":")
        if not colon:
            return None
        header[key.strip()] = value.strip()
    return header, answer[end + 4 :].strip()


def _read_page(document: pdfium.PdfDocument, index: int) -> tuple[bytes, str]:
    """PNG страницы с длинной стороной `LONGEST_SIDE` и её текстовый слой."""
    page = document[index]
    try:
        longest = max(page.get_size())
        scale = LONGEST_SIDE / longest
        # pypdfium2 округляет размер картинки вверх, и ошибка округления дала бы 1289.
        while math.ceil(longest * scale) > LONGEST_SIDE:
            scale = math.nextafter(scale, 0)
        bitmap = page.render(scale=scale)
        try:
            buffer = io.BytesIO()
            bitmap.to_pil().save(buffer, format="PNG")
        finally:
            bitmap.close()
        textpage = page.get_textpage()
        try:
            layer_text: str = textpage.get_text_bounded()
        finally:
            textpage.close()
    finally:
        page.close()
    return buffer.getvalue(), layer_text


def build_docparse_client(settings: Settings, recorder: RunRecorder) -> DocparseClient:
    """Собирает клиент docparse из узлов `COMPASS_DOCPARSE_URLS`.

    Raises:
        AiNotConfiguredError: Адреса узлов docparse не заданы.
    """
    timeout = httpx2.Timeout(
        settings.docparse_timeout_seconds, connect=settings.ai_connect_timeout_seconds
    )
    return DocparseClient(node_urls(settings, "docparse"), recorder, timeout=timeout)


__all__ = [
    "LONGEST_SIDE",
    "MODEL_ALIAS",
    "PROMPT",
    "PROMPT_VERSION",
    "DocparseClient",
    "DocparsePageError",
    "ParsedPage",
    "ParsedPdf",
    "build_docparse_client",
]
