"""Замер сервера эмбеддингов: задержка запроса, скорость индексации, память (спека #16, раздел 6).

    uv run --package clin-compass-eval python eval/embeddings/bench.py --base-url http://<адрес>:8001

Запросы идут через клиент core/ai, поэтому уходят с настоящим префиксом инструкции.
Память меряется, только если llama-server слушает порт на этом же Маке. RSS включает веса,
которые llama.cpp отображает через mmap; footprint — всё остальное: KV-кэш и буферы.
Результат — таблица в markdown для README сервиса.
"""

import argparse
import asyncio
import itertools
import logging
import re
import statistics
import subprocess
import time
from urllib.parse import urlparse

from common import configure_logging, embedder_for, load_corpus, read_versions

WARMUP_QUERIES = 5
_GB_PER_UNIT = {"KB": 1 / 1024**2, "MB": 1 / 1024, "GB": 1.0}
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}

log = logging.getLogger(__name__)


def _num(value: float, digits: int = 1) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def _run(*command: str) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return ""
    return result.stdout.strip()


def _is_this_mac(host: str) -> bool:
    """Сервер на этом Маке: loopback или собственный tailnet-адрес."""
    return host in _LOOPBACK or host == _run("tailscale", "ip", "-4")


def _server_memory_gb(port: int) -> tuple[float, float] | None:
    """RSS и phys_footprint процесса на порту этого Мака, в ГБ. None — порт никто не слушает."""
    pids = _run("lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t").split()
    if not pids:
        return None
    rss_gb = int(_run("ps", "-o", "rss=", "-p", pids[0])) / 1024**2
    match = re.search(r"phys_footprint:\s*([\d.]+)\s*(KB|MB|GB)", _run("footprint", "-p", pids[0]))
    footprint_gb = float(match.group(1)) * _GB_PER_UNIT[match.group(2)] if match else float("nan")
    return rss_gb, footprint_gb


async def run(base_url: str, queries: int, fragments: int, batch: int) -> None:
    """Меряет сервер и пишет таблицу в лог."""
    corpus = load_corpus()
    embedder = embedder_for(base_url)

    for text in corpus.queries[:WARMUP_QUERIES]:
        await embedder.embed_query(text)

    latencies_ms = []
    for text in itertools.islice(itertools.cycle(corpus.queries), queries):
        started = time.perf_counter()
        await embedder.embed_query(text)
        latencies_ms.append((time.perf_counter() - started) * 1000)

    # Фрагментов в секунду зависит от их длины, поэтому считаем и токены: их можно сравнивать
    # между корпусами и с замерами ADR-0007.
    texts = list(itertools.islice(itertools.cycle(corpus.fragments), fragments))
    tokens = 0
    started = time.perf_counter()
    for i in range(0, len(texts), batch):
        result = await embedder.embed_documents(texts[i : i + batch])
        tokens += result.usage.input_tokens
    elapsed = time.perf_counter() - started
    per_second = len(texts) / elapsed

    versions = read_versions()
    mac = f"{_run('sysctl', '-n', 'hw.model')}, {_run('sysctl', '-n', 'machdep.cpu.brand_string')}"
    memsize_gb = int(_run("sysctl", "-n", "hw.memsize")) / 1024**3
    median = statistics.median(latencies_ms)
    p95 = statistics.quantiles(latencies_ms, n=20)[18]

    # Железо и память известны только для этого Мака. Если сервер на другом, таблица об этом
    # говорит прямо, а задержка включает сеть между Маками.
    url = urlparse(base_url)
    host = url.hostname or ""
    local = _is_this_mac(host)
    memory = _server_memory_gb(url.port or 80) if local else None
    if not local:
        mac_rows = [f"| Сервер | {host}, другой Мак |", f"| Замер запущен с | {mac} |"]
        memory_row = "| Память llama-server | не меряли: сервер на другом Маке |"
    elif memory is None:
        mac_rows = [f"| Мак | {mac}, {memsize_gb:.0f} ГБ |"]
        memory_row = f"| Память llama-server | не нашли процесс на порту {url.port} |"
    else:
        rss_gb, footprint_gb = memory
        mac_rows = [f"| Мак | {mac}, {memsize_gb:.0f} ГБ |"]
        memory_row = (
            "| Память llama-server: RSS / без весов (footprint) | "
            f"{_num(rss_gb)} / {_num(footprint_gb)} ГБ |"
        )

    rows = [
        "| Что | Значение |",
        "|---|---|",
        *mac_rows,
        f"| llama.cpp | `{versions['LLAMA_CPP_COMMIT'][:7]}` |",
        f"| GGUF | `{versions['GGUF_FILE']}`, SHA-256 `{versions['GGUF_SHA256'][:12]}…` |",
        f"| Запрос: медиана / p95, {queries} шт. | {_num(median, 0)} / {_num(p95, 0)} мс |",
        f"| Индексация: {fragments} фрагментов по ~{tokens // len(texts)} токенов, "
        f"батчами по {batch} | {_num(per_second)} фрагмента/с, {tokens / elapsed:.0f} ток/с |",
        memory_row,
    ]
    log.info("\n".join(rows))


def main() -> None:
    """Точка входа."""
    configure_logging()
    parser = argparse.ArgumentParser(description="Замер сервера эмбеддингов")
    parser.add_argument("--base-url", required=True, help="например http://100.x.y.z:8001")
    parser.add_argument("--queries", type=int, default=50, help="число одиночных запросов")
    parser.add_argument("--fragments", type=int, default=200, help="число фрагментов индексации")
    parser.add_argument("--batch", type=int, default=4, help="фрагментов в одном запросе")
    args = parser.parse_args()
    asyncio.run(run(args.base_url, args.queries, args.fragments, args.batch))


if __name__ == "__main__":
    main()
