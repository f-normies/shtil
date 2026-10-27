"""Замер сервиса ASR: RTF и память (спека #17, раздел 9).

    uv run --package clin-compass-eval python eval/asr/bench.py --base-url http://<адрес>:8002

Речь синтезирует `say -v Milena`, поэтому нужен macOS. PyAV кодирует её так же, как браузер:
webm/opus 32 кбит/с и mp4/aac 64 кбит/с. Записи живут во временном каталоге. Записей две:
короткая — первая фраза, длинная — все фразы с паузой 1 с, она проходит через VAD.
RTF = медиана времени ответа / длительность записи. Память (RSS) меряется, только если
сервис слушает порт на этом же Маке. Результат — таблица в markdown для README сервиса.
"""

import argparse
import io
import json
import logging
import statistics
import subprocess
import tempfile
import time
from fractions import Fraction
from pathlib import Path
from urllib.parse import urlparse

import av
import httpx
import numpy as np
import numpy.typing as npt

HERE = Path(__file__).resolve().parent
PHRASES_PATH = HERE / "phrases.json"
VERSIONS_PATH = HERE.parents[1] / "services" / "asr" / "versions.env"
ENDPOINT = "/v1/audio/transcriptions"
SAMPLE_RATE = 16_000
VOICE = "Milena"
# Название, контейнер PyAV, кодек, битрейт, MIME-тип — как у MediaRecorder в браузерах.
FORMATS = [
    ("webm/opus", "webm", "libopus", 32_000, "audio/webm"),
    ("mp4/aac", "mp4", "aac", 64_000, "audio/mp4"),
]
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}

Samples = npt.NDArray[np.float32]
log = logging.getLogger(__name__)


def _num(value: float, digits: int = 1) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def _run(*command: str) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return ""
    return result.stdout.strip()


def speak(text: str, workdir: Path) -> Samples:
    """Синтезирует фразу голосом Milena в float32, 16 кГц, моно."""
    wav = workdir / "phrase.wav"
    subprocess.run(
        ["say", "-v", VOICE, "--file-format=WAVE", "--data-format=LEF32@16000", "-o", wav, text],
        check=True,
    )
    with av.open(str(wav)) as container:
        frames = [frame.to_ndarray().reshape(-1) for frame in container.decode(audio=0)]
    return np.concatenate(frames).astype(np.float32)


def encode(samples: Samples, *, container: str, codec: str, bit_rate: int) -> bytes:
    """Кодирует моно 16 кГц в контейнер, как браузер кодирует запись с микрофона."""
    buf = io.BytesIO()
    with av.open(buf, mode="w", format=container) as out:
        stream = out.add_stream(codec, rate=SAMPLE_RATE, layout="mono")
        stream.bit_rate = bit_rate
        frame_size = stream.codec_context.frame_size or 1024
        for start in range(0, len(samples), frame_size):
            chunk = samples[start : start + frame_size].reshape(1, -1)
            frame = av.AudioFrame.from_ndarray(chunk, format="flt", layout="mono")
            frame.sample_rate = SAMPLE_RATE
            frame.pts = start
            frame.time_base = Fraction(1, SAMPLE_RATE)
            for packet in stream.encode(frame):
                out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)
    return buf.getvalue()


def server_rss_gb(base_url: str) -> float | None:
    """RSS процесса, который слушает порт сервиса на этом Маке, в ГБ. None — сервис не здесь."""
    url = urlparse(base_url)
    if url.hostname not in _LOOPBACK and url.hostname != _run("tailscale", "ip", "-4"):
        return None
    pids = _run("lsof", "-nP", f"-iTCP:{url.port or 80}", "-sTCP:LISTEN", "-t").split()
    if not pids:
        return None
    return int(_run("ps", "-o", "rss=", "-p", pids[0])) / 1024**2


def _model_name() -> str:
    for line in VERSIONS_PATH.read_text(encoding="utf-8").splitlines():
        if line.startswith("MODEL_NAME="):
            return line.partition("=")[2]
    return "?"


def _records() -> list[tuple[str, Samples]]:
    phrases: list[str] = json.loads(PHRASES_PATH.read_text(encoding="utf-8"))["phrases"]
    with tempfile.TemporaryDirectory() as tmp:
        speech = [speak(phrase, Path(tmp)) for phrase in phrases]
    pause = np.zeros(SAMPLE_RATE, dtype=np.float32)
    long = np.concatenate([part for phrase in speech for part in (phrase, pause)])
    return [("короткая", speech[0]), ("длинная", long)]


def run(base_url: str, runs: int, hotword: str) -> None:
    """Меряет сервис и пишет таблицу в лог."""
    records = _records()
    rss_start = server_rss_gb(base_url)
    rows = []
    with httpx.Client(base_url=base_url, timeout=300) as client:
        for record_name, samples in records:
            for format_name, container, codec, bit_rate, mime in FORMATS:
                data = encode(samples, container=container, codec=codec, bit_rate=bit_rate)
                for mode, hotwords in (("без подсказок", []), (f"«{hotword}»", [hotword])):
                    latencies = []
                    duration = 0.0
                    for attempt in range(runs + 1):  # первый запрос — прогрев
                        started = time.perf_counter()
                        response = client.post(
                            ENDPOINT,
                            files={"file": (f"record.{container}", data, mime)},
                            data={"hotwords": hotwords},
                        )
                        elapsed = time.perf_counter() - started
                        response.raise_for_status()
                        duration = response.json()["duration"]
                        if attempt:
                            latencies.append(elapsed)
                    median = statistics.median(latencies)
                    rows.append(
                        f"| {record_name} | {format_name} | {_num(duration)} с | {mode} "
                        f"| {_num(median * 1000, 0)} мс | {_num(median / duration, 3)} |"
                    )
    rss_end = server_rss_gb(base_url)

    mac = f"{_run('sysctl', '-n', 'hw.model')}, {_run('sysctl', '-n', 'machdep.cpu.brand_string')}"
    memsize_gb = int(_run("sysctl", "-n", "hw.memsize")) / 1024**3
    if rss_start is None or rss_end is None:
        memory = "не меряли: сервис на другом Маке или не найден по порту"
    else:
        memory = f"{_num(rss_start, 2)} ГБ после старта, {_num(rss_end, 2)} ГБ после прогонов"
    table = [
        f"Мак: {mac}, {memsize_gb:.0f} ГБ. Сервис: {base_url}. Модель: `{_model_name()}`.",
        f"Память сервиса (RSS): {memory}. На каждую строку — 1 прогрев и {runs} замеров.",
        "",
        "| Запись | Формат | Длительность | Подсказки | Ответ, медиана | RTF |",
        "|---|---|---|---|---|---|",
        *rows,
    ]
    log.info("\n".join(table))


def main() -> None:
    """Точка входа."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(description="Замер сервиса ASR")
    parser.add_argument("--base-url", required=True, help="например http://100.x.y.z:8002")
    parser.add_argument("--runs", type=int, default=5, help="замеров на каждую строку")
    parser.add_argument("--hotword", default="Авиандр", help="подсказка для второго режима")
    args = parser.parse_args()
    run(args.base_url, args.runs, args.hotword)


if __name__ == "__main__":
    main()
