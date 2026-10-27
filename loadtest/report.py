"""Таблица по ступеням и график «время ответа от числа пользователей» (#41).

Читает каталоги прогонов, скопированные с ВМ (results/<имя>/ из run.sh). Ступень берётся
без первых 30 с: в них Locust добавляет пользователей и они входят по паролю.

    uv run --with matplotlib python loadtest/report.py --out <каталог> \\
        --run "1 реплика=<каталог>=100:90,200:90" --run "2 реплики=<каталог>=100:90,200:90"
"""

import argparse
import csv
import statistics
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

WARMUP_SECONDS = 30
# На графике — только ступени, где ошибок не больше 5%.
MAX_FAILURES = 5.0
COLORS = ["#2a78d6", "#eb6834", "#1baf7a"]


@dataclass(frozen=True)
class Step:
    """Итог одной ступени нагрузки."""

    users: int
    rps: float
    p50: float
    p95: float
    p99: float
    failures: float
    alert_mean: float | None
    alert_max: float | None
    alert_lost: int
    api_cpu: float
    db_cpu: float
    worker_cpu: float
    locust_cpu: float
    db_active: float


def _num(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


def _windows(stages: str, start: float) -> list[tuple[int, float, float]]:
    out, t = [], start
    for part in stages.split(","):
        users, seconds = (int(x) for x in part.split(":"))
        out.append((users, t + WARMUP_SECONDS, t + seconds))
        t += seconds
    return out


def _cpu(rows: list[dict], prefix: str, lo: float, hi: float) -> float:
    """Средняя загрузка контейнеров с префиксом имени, в ядрах (сумма по репликам)."""
    per_ts: dict[int, float] = defaultdict(float)
    for r in rows:
        ts = int(r["ts"])
        if lo <= ts < hi and r["name"].startswith(prefix):
            per_ts[ts] += float(r["cpu"].rstrip("%")) / 100
    return statistics.mean(per_ts.values()) if per_ts else 0.0


def _rows(path: Path) -> list[dict]:
    with path.open() as f:
        return list(csv.DictReader(f))


def analyse(directory: Path, stages: str) -> list[Step]:
    history = _rows(directory / "locust_stats_history.csv")
    stats = _rows(directory / "stats.csv")
    db = _rows(directory / "db.csv")
    start = int(history[0]["Timestamp"])
    steps = []
    for users, lo, hi in _windows(stages, start):
        agg = [
            r
            for r in history
            if r["Name"] == "Aggregated" and lo <= int(r["Timestamp"]) < hi
        ]
        alert = [
            r
            for r in history
            if r["Name"] == "WS alert.created"
            and lo - WARMUP_SECONDS <= int(r["Timestamp"]) < hi
        ]
        if not agg:
            break
        first, last = agg[0], agg[-1]
        span = int(last["Timestamp"]) - int(first["Timestamp"]) or 1
        requests = float(last["Total Request Count"]) - float(
            first["Total Request Count"]
        )
        failed = float(last["Total Failure Count"]) - float(
            first["Total Failure Count"]
        )
        steps.append(
            Step(
                users=users,
                rps=(requests - failed) / span,
                p50=statistics.median(
                    v for r in agg if (v := _num(r["50%"])) is not None
                ),
                p95=statistics.median(
                    v for r in agg if (v := _num(r["95%"])) is not None
                ),
                p99=statistics.median(
                    v for r in agg if (v := _num(r["99%"])) is not None
                ),
                failures=100 * failed / requests if requests else 0.0,
                **_alerts(directory, alert, lo - WARMUP_SECONDS, hi),
                api_cpu=_cpu(stats, "clin-compass-lt-api", lo, hi),
                db_cpu=_cpu(stats, "clin-compass-lt-postgres", lo, hi),
                worker_cpu=_cpu(stats, "clin-compass-lt-worker", lo, hi),
                locust_cpu=_cpu(stats, "shtil-locust", lo, hi),
                db_active=_mean(
                    float(r["active"])
                    for r in db
                    if r["active"] and lo <= int(r["ts"]) < hi
                ),
            )
        )
    return steps


def _mean(values: object) -> float:
    items = list(values)  # type: ignore[call-overload]
    return statistics.mean(items) if items else 0.0


def _alerts(directory: Path, rows: list[dict], lo: float, hi: float) -> dict:
    """Доставка алерта за ступень. Есть alerts.csv — по каждому замеру, иначе только среднее
    из накопленных счётчиков Locust."""
    path = directory / "alerts.csv"
    if not path.exists():
        return {"alert_mean": _alert_mean(rows), "alert_max": None, "alert_lost": 0}
    probes = [line.split(",") for line in path.read_text().split()]
    window = [(float(ms), ok == "1") for ts, ms, ok in probes if lo <= int(ts) < hi]
    delivered = [ms for ms, ok in window if ok]
    return {
        "alert_mean": statistics.mean(delivered) if delivered else None,
        "alert_max": max(delivered) if delivered else None,
        "alert_lost": sum(1 for _, ok in window if not ok),
    }


def _alert_mean(rows: list[dict]) -> float | None:
    """Среднее время доставки алерта за ступень из накопленных счётчика и среднего."""
    if len(rows) < 2:
        return None
    n1, n2 = (
        float(rows[0]["Total Request Count"]),
        float(rows[-1]["Total Request Count"]),
    )
    a1, a2 = (
        float(rows[0]["Total Average Response Time"]),
        float(rows[-1]["Total Average Response Time"]),
    )
    return (a2 * n2 - a1 * n1) / (n2 - n1) if n2 > n1 else None


def table(label: str, steps: list[Step]) -> str:
    lines = [
        f"### {label}",
        "",
        (
            "| Пользователи | Успешных запросов/с | p50, мс | p95, мс | p99, мс | Ошибки, % "
            "| Алерт, мс (ср./макс.) | api, ядер | Postgres, ядер | worker, ядер "
            "| Locust, ядер | Активных запросов БД |"
        ),
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for s in steps:
        alert = "—" if s.alert_mean is None else f"{s.alert_mean:.0f}"
        if s.alert_max is not None:
            alert += f" / {s.alert_max:.0f}"
        if s.alert_lost:
            alert += f", не дошло {s.alert_lost}"
        lines.append(
            f"| {s.users} | {s.rps:.0f} | {s.p50:.0f} | {s.p95:.0f} | {s.p99:.0f} "
            f"| {s.failures:.2f} | {alert} | {s.api_cpu:.2f} | {s.db_cpu:.2f} "
            f"| {s.worker_cpu:.2f} | {s.locust_cpu:.2f} | {s.db_active:.1f} |"
        )
    return "\n".join(lines)


def chart(runs: dict[str, list[Step]], out: Path) -> None:
    """p95 и пропускная способность. Цвет — число реплик api, пунктир — код до исправления."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8), dpi=160)
    for label, steps in runs.items():
        color = COLORS[1] if label.startswith("2") else COLORS[0]
        style = "--" if "до исправления" in label else "-"
        # Ступени с отказами не показываем: время быстрых ответов 502 не время ответа системы.
        steps = [s for s in steps if s.failures <= MAX_FAILURES]
        users = [s.users for s in steps]
        for ax, values in (
            (axes[0], [s.p95 for s in steps]),
            (axes[1], [s.rps for s in steps]),
        ):
            ax.plot(
                users,
                values,
                color=color,
                ls=style,
                lw=2,
                marker="o",
                ms=5,
                label=label,
            )
    axes[0].axhline(1000, color="#8a8984", lw=1, ls=":")
    axes[0].text(
        1250, 1150, "1 с — граница деградации", ha="right", color="#5f5e5a", fontsize=9
    )
    axes[0].set_title("Время ответа p95, мс (лог. шкала)", loc="left", fontsize=11)
    axes[0].set_yscale("log")
    axes[1].set_title("Успешных запросов в секунду", loc="left", fontsize=11)
    for ax in axes:
        ax.set_xlabel("Одновременных пользователей (врачи : пациенты = 1 : 20)")
        ax.grid(color="#e4e3df", lw=0.8)
        ax.spines[["top", "right"]].set_visible(False)
    axes[1].legend(frameon=False, fontsize=9)
    fig.tight_layout()
    fig.savefig(out / "latency-vs-users.png")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--run",
        action="append",
        required=True,
        help="подпись=каталог=ступени, как в run.sh",
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    runs = {}
    for item in args.run:
        label, directory, stages = item.split("=", 2)
        runs[label] = analyse(Path(directory), stages)
    args.out.mkdir(parents=True, exist_ok=True)
    text = "\n\n".join(table(label, steps) for label, steps in runs.items())
    (args.out / "steps.md").write_text(text + "\n")
    chart(runs, args.out)
    print(text)


if __name__ == "__main__":
    main()
