"""Нагрузочный тест Штиля (#41): пациенты, врачи и замер доставки алерта по WebSocket.

Учётные записи создаёт `prepare.py`. Пациент и врач повторяют запросы экранов кабинетов,
каждый держит WebSocket, как открытое приложение. `AlertProbe` один на весь тест: пациент
замера отправляет PHQ-9 с пунктом 9 ≥ 1, врач замера ждёт `alert.created` в сокете и
закрывает сигнал. Время от отправки ответа до события — строка `WS alert.created`.

Переменные: LOADTEST_PASSWORD — пароль всех учётных записей; LOADTEST_STAGES — ступени
«пользователи:секунды» через запятую; LOADTEST_SPAWN_RATE — пользователей в секунду.
"""

import os
import random
import time
import uuid
from datetime import UTC, date, datetime, timedelta

import gevent
from accounts import (
    DOCTORS,
    PATIENTS_PER_DOCTOR,
    PROBE_DOCTOR,
    PROBE_PATIENT,
    doctor_email,
    patient_email,
)
from gevent.event import AsyncResult
from locust import FastHttpUser, LoadTestShape, between, task
from session import Session, fire

PROBE_SECONDS = float(os.environ.get("LOADTEST_PROBE_SECONDS", "10"))
ALERT_TIMEOUT = 30.0
# Каждый замер алерта отдельной строкой: «время Unix,мс,доставлен» — для таблицы по ступеням.
ALERTS_CSV = os.environ.get("LOADTEST_ALERTS_CSV")
MOOD_WORDS = ["спокойствие", "радость", "интерес", "усталость", "тревога", "плохой сон"]
CHAT_TEXTS = ["Добрый день, всё по плану", "Спала лучше", "Вопрос по приёму вечером"]


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


class AppUser(FastHttpUser):
    """Общее для кабинетов: номер учётной записи, вход, сокет."""

    abstract = True
    insecure = True
    # Каждый пользователь держит свои соединения: так ведёт себя отдельный браузер.
    concurrency = 4
    running = False
    _counter = 0

    def next_index(self) -> int:
        """Номер учётной записи: у процессов Locust разные смещения, чтобы не совпадать."""
        runner = self.environment.runner
        worker = getattr(runner, "worker_index", 0)
        processes = int(os.environ.get("LOADTEST_PROCESSES", "1"))
        index = AppUser._counter * processes + worker
        AppUser._counter += 1
        return index

    def start_session(self, email: str) -> Session | None:
        self.running = True
        session = Session(self, email)
        if not session.login():
            return None
        session.connect()
        return session

    def on_stop(self) -> None:
        self.running = False
        for session in getattr(self, "sessions", []):
            session.close()


class Patient(AppUser):
    """Пациент в PWA: главный экран, дневник настроения, шкалы, чат."""

    weight = 20
    wait_time = between(5, 15)

    def on_start(self) -> None:
        index = self.next_index() % (DOCTORS * PATIENTS_PER_DOCTOR)
        email = patient_email(
            index // PATIENTS_PER_DOCTOR + 1, index % PATIENTS_PER_DOCTOR + 1
        )
        self.s = self.start_session(email)
        self.sessions = [self.s] if self.s else []
        self.doctor_id = None
        self.scales: dict[str, dict] = {}
        if self.s is None:
            self.stop()
            return
        status = self.s.get(
            f"/api/v1/patients/{self.s.me['id']}/messaging-status",
            "GET /patients/[me]/messaging-status",
        )
        self.doctor_id = status and status.get("doctor_id")

    @task(4)
    def home(self) -> None:
        self.s.get("/api/v1/auth/me", "GET /auth/me")
        self.s.get("/api/v1/assessments/schedule", "GET /assessments/schedule")
        self.s.get("/api/v1/me/prescriptions", "GET /me/prescriptions")
        self.s.get("/api/v1/me/doses", "GET /me/doses")
        self.s.get("/api/v1/diary/context", "GET /diary/context")

    @task(3)
    def mood(self) -> None:
        self.s.get("/api/v1/diary/entries", "GET /diary/entries")
        self.s.post(
            "/api/v1/diary/entries",
            "POST /diary/entries (mood)",
            {
                "operation_id": str(uuid.uuid4()),
                "entry_id": str(uuid.uuid4()),
                "kind": "mood",
                "occurred_at": now_iso(),
                "mood_level": random.randint(2, 5),
                "mood_words": random.sample(MOOD_WORDS, 2),
            },
        )

    @task(1)
    def assessment(self) -> None:
        scale_id = random.choice(["phq-9", "gad-7"])
        scale = self.scales.get(scale_id)
        if scale is None:
            out = self.s.get(
                f"/api/v1/assessments/scales/{scale_id}", "GET /assessments/scales/[id]"
            )
            if out is None:
                return
            scale = self.scales[scale_id] = out["scale"]
        # Лёгкие ответы, пункт 9 PHQ-9 — 0: алерты создаёт только AlertProbe.
        answers = {item["id"]: random.choice([0, 0, 1]) for item in scale["items"]}
        answers.pop("q9", None)
        if scale_id == "phq-9":
            answers["q9"] = 0
        self.s.post(
            "/api/v1/assessments",
            "POST /assessments",
            {
                "patient_id": self.s.me["id"],
                "submission_id": str(uuid.uuid4()),
                "scale_id": scale_id,
                "scale_version": scale["version"],
                "completed_at": now_iso(),
                "answers": answers,
            },
        )
        self.s.get("/api/v1/assessments", "GET /assessments")

    @task(1)
    def chat(self) -> None:
        me = self.s.me["id"]
        self.s.get(
            f"/api/v1/patients/{me}/messages", "GET /patients/[me]/messages", limit=50
        )
        if self.doctor_id and random.random() < 0.3:
            self.s.post(
                f"/api/v1/patients/{me}/messages",
                "POST /patients/[me]/messages",
                {
                    "expected_doctor_id": self.doctor_id,
                    "client_message_id": str(uuid.uuid4()),
                    "text": random.choice(CHAT_TEXTS),
                },
            )


class Doctor(AppUser):
    """Врач в кабинете: список пациентов, алерты, карточка пациента, ответ в чате."""

    weight = 1
    wait_time = between(5, 15)

    def on_start(self) -> None:
        self.s = self.start_session(doctor_email(self.next_index() % DOCTORS + 1))
        self.sessions = [self.s] if self.s else []
        self.patients: list[str] = []
        if self.s is None:
            self.stop()

    @task(2)
    def dashboard(self) -> None:
        patients = self.s.get("/api/v1/patients", "GET /patients")
        if patients:
            self.patients = [p["id"] for p in patients]
        self.s.get("/api/v1/alerts", "GET /alerts")
        since = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        self.s.get("/api/v1/proposals/summary", "GET /proposals/summary", since=since)
        self.s.get("/api/v1/messaging/unread", "GET /messaging/unread")

    @task(5)
    def patient_card(self) -> None:
        if not self.patients:
            return self.dashboard()
        pid = random.choice(self.patients)
        base = f"/api/v1/patients/{pid}"
        # Окно графика врача — 30 дней. Пациентам теста начало наблюдения сдвинуто на 60 дней
        # назад (loadtest/README.md), иначе окно выходит за наблюдение и API отвечает 422.
        end = date.today()
        period = {
            "start": (end - timedelta(days=30)).isoformat(),
            "end": end.isoformat(),
        }
        self.s.get(base, "GET /patients/[id]")
        for part in (
            "assessments",
            "assessments/schedule",
            "alerts",
            "adherence",
            "prescriptions",
            "medications",
            "diagnoses",
            "proposals",
            "summary",
            "therapy/guidance",
        ):
            self.s.get(f"{base}/{part}", f"GET /patients/[id]/{part}")
        self.s.get(f"{base}/diary", "GET /patients/[id]/diary", **period)
        self.s.get(f"{base}/diary/moods", "GET /patients/[id]/diary/moods", **period)
        self.s.get(f"{base}/messages", "GET /patients/[id]/messages", limit=50)
        if random.random() < 0.2:
            self.s.post(
                f"{base}/messages",
                "POST /patients/[id]/messages",
                {
                    "expected_doctor_id": self.s.me["id"],
                    "client_message_id": str(uuid.uuid4()),
                    "text": "Спасибо, продолжаем по плану",
                },
            )


class AlertProbe(AppUser):
    """Замер доставки алерта: ответ PHQ-9 пациента → `alert.created` в сокете врача."""

    fixed_count = 1
    wait_time = between(PROBE_SECONDS, PROBE_SECONDS)

    def on_start(self) -> None:
        self.doctor = self.start_session(PROBE_DOCTOR)
        self.patient = self.start_session(PROBE_PATIENT)
        self.sessions = [s for s in (self.doctor, self.patient) if s]
        self.pending: AsyncResult | None = None
        if self.doctor is None or self.patient is None:
            self.stop()
            return
        self.doctor.on_event = self.on_event
        out = self.patient.get(
            "/api/v1/assessments/scales/phq-9", "GET /assessments/scales/[id]"
        )
        self.scale = out["scale"]
        gevent.sleep(2)

    def on_event(self, event: dict) -> None:
        if event.get("type") == "alert.created" and self.pending is not None:
            self.pending.set(event["data"]["alert_id"])

    @task
    def probe(self) -> None:
        self.pending = AsyncResult()
        answers = {item["id"]: 0 for item in self.scale["items"]}
        answers["q9"] = 1
        started = time.perf_counter()
        sent = self.patient.post(
            "/api/v1/assessments",
            "POST /assessments (probe q9=1)",
            {
                "patient_id": self.patient.me["id"],
                "submission_id": str(uuid.uuid4()),
                "scale_id": "phq-9",
                "scale_version": self.scale["version"],
                "completed_at": now_iso(),
                "answers": answers,
            },
        )
        if sent is None:
            return
        try:
            alert_id = self.pending.get(timeout=ALERT_TIMEOUT)
        except gevent.Timeout as error:
            fire("WS alert.created", started, error)
            self._record(started, delivered=False)
            self._resolve_open()
            return
        fire("WS alert.created", started)
        self._record(started, delivered=True)
        self._resolve(alert_id)

    def _record(self, started: float, *, delivered: bool) -> None:
        if ALERTS_CSV is None:
            return
        elapsed = (time.perf_counter() - started) * 1000
        with open(ALERTS_CSV, "a") as out:
            out.write(f"{time.time():.0f},{elapsed:.0f},{int(delivered)}\n")

    def _resolve(self, alert_id: str) -> None:
        self.doctor.post(
            f"/api/v1/alerts/{alert_id}/acknowledge", "POST /alerts/[id]/acknowledge"
        )
        self.doctor.post(
            f"/api/v1/alerts/{alert_id}/resolve",
            "POST /alerts/[id]/resolve",
            {"decision_id": "risk-not-confirmed", "reason": "нагрузочный тест"},
        )

    def _resolve_open(self) -> None:
        """Событие не дошло: закрыть открытый сигнал, иначе следующий замер его только дополнит."""
        for alert in self.doctor.get("/api/v1/alerts", "GET /alerts") or []:
            if alert.get("state") != "resolved" and alert.get("kind") == "alert":
                self._resolve(alert["id"])


class Steps(LoadTestShape):
    """Ступени нагрузки из LOADTEST_STAGES: «пользователи:секунды,…»."""

    def __init__(self) -> None:
        super().__init__()
        raw = os.environ.get("LOADTEST_STAGES", "20:60")
        self.rate = float(os.environ.get("LOADTEST_SPAWN_RATE", "10"))
        self.stages = []
        total = 0
        for part in raw.split(","):
            users, seconds = part.split(":")
            total += int(seconds)
            self.stages.append((total, int(users)))

    def tick(self) -> tuple[int, float] | None:
        run_time = self.get_run_time()
        for until, users in self.stages:
            if run_time < until:
                return users, self.rate
        return None
