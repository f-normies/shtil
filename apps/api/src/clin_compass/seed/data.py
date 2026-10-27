"""Кого и с какими ответами создаёт `make seed` (спека #19, раздел 3).

Все люди вымышлены, совпадение ФИО с реальными людьми случайно. Адреса — на зарезервированном
домене example.com. Телефоны — +7 000 …: кода зоны на 0 в России нет, звонок по ссылке
из карточки врача никуда не уйдёт. Ответы — баллы пунктов по порядку: GAD-7 q1…q7,
PHQ-9 q1…q9 (0 — «совсем нет», 3 — «почти каждый день»). Сумма в комментарии — общий балл.

Врач демо — doctor@example.com. У его пациентов пункт 9 PHQ-9 всегда 0: ответ «1» на шаге s8
сценария 1 отправляется в демо вживую, и его сигнал должен быть у врача единственным
«обязательно».
"""

from dataclasses import dataclass
from datetime import date
from typing import Literal


@dataclass(frozen=True)
class Record:
    """Одна заполненная шкала: сколько дней назад и ответы на пункты по порядку."""

    scale_id: str
    days_ago: int
    answers: tuple[int, ...]


@dataclass(frozen=True)
class SeedDoctor:
    """Врач сида."""

    email: str
    full_name: str


@dataclass(frozen=True)
class SeedDiagnosis:
    """Диагноз МКБ-10, который в сиде ставит ведущий врач."""

    icd10: str
    title: str


@dataclass(frozen=True)
class SeedMedication:
    """Препарат вне системы. `stopped_days_ago` — сколько дней назад отменён, `None` — принимает."""

    name: str
    stopped_days_ago: int | None = None


@dataclass(frozen=True)
class SeedCourse:
    """Назначение по подбору справочника `days_ago` дней назад и отметки доз (спека #34, 8).

    Все дозы приняты, кроме пропущенных вечерних и утренних без отметки — по дням назад.
    Сегодня отмечено только утро.
    """

    days_ago: int
    missed_evenings: tuple[int, ...] = ()
    unmarked_mornings: tuple[int, ...] = ()


@dataclass(frozen=True)
class SeedPatient:
    """Пациент сида: ведущий врач, телефон в E.164, записи шкал и клинический профиль."""

    email: str
    full_name: str
    doctor_email: str
    phone: str | None
    records: tuple[Record, ...]
    birth_date: date | None = None
    sex: Literal["female", "male"] | None = None
    diagnoses: tuple[SeedDiagnosis, ...] = ()
    medications: tuple[SeedMedication, ...] = ()
    course: SeedCourse | None = None


@dataclass(frozen=True)
class Plan:
    """Всё, что создаёт сид."""

    doctors: tuple[SeedDoctor, ...]
    patients: tuple[SeedPatient, ...]


def gad7(days_ago: int, *answers: int) -> Record:
    """Запись GAD-7: ответы на q1…q7."""
    return Record("gad-7", days_ago, answers)


def phq9(days_ago: int, *answers: int) -> Record:
    """Запись PHQ-9: ответы на q1…q9."""
    return Record("phq-9", days_ago, answers)


GAD = SeedDiagnosis("F41.1", "Генерализованное тревожное расстройство")

DEMO_DOCTOR = "doctor@example.com"
SECOND_DOCTOR = "doctor2@example.com"

PLAN = Plan(
    doctors=(
        SeedDoctor(DEMO_DOCTOR, "Соколова Ирина Павловна"),
        SeedDoctor(SECOND_DOCTOR, "Громов Алексей Николаевич"),
    ),
    patients=(
        # Сценарий 1 (knowledge/scenarios/scenario-1-aviandr.yaml): женщина 34 лет с ГТР.
        # s2 — первое заполнение, s7 — через 2 недели на «Авиандре». s8 — вживую в демо.
        SeedPatient(
            "patient1@example.com",
            "Лебедева Марина Викторовна",
            DEMO_DOCTOR,
            "+70000000001",
            (
                gad7(16, 2, 2, 2, 2, 2, 2, 2),  # 14
                phq9(16, 1, 1, 1, 1, 1, 1, 1, 1, 0),  # 8
                gad7(2, 1, 1, 2, 1, 1, 2, 1),  # 9
                phq9(2, 1, 1, 1, 1, 1, 1, 0, 0, 0),  # 6
            ),
            # Сценарий 1: 34 года на день демо, предварительный диагноз F41.1 (шаг s4).
            birth_date=date(1992, 3, 14),
            sex="female",
            diagnoses=(GAD,),
        ),
        # Улучшение за 8 недель: тревога 12 → 4, депрессия 10 → 3.
        SeedPatient(
            "patient3@example.com",
            "Миронова Елена Игоревна",
            DEMO_DOCTOR,
            "+70000000003",
            (
                gad7(52, 2, 2, 2, 2, 1, 2, 1),  # 12
                phq9(52, 1, 2, 1, 2, 1, 1, 1, 1, 0),  # 10
                gad7(45, 2, 2, 2, 1, 1, 2, 1),  # 11
                phq9(45, 1, 1, 1, 2, 1, 1, 1, 1, 0),  # 9
                gad7(38, 1, 2, 1, 1, 1, 2, 1),  # 9
                phq9(38, 1, 1, 1, 1, 1, 1, 1, 1, 0),  # 8
                gad7(31, 1, 1, 1, 1, 1, 2, 1),  # 8
                phq9(31, 1, 1, 1, 1, 1, 1, 1, 0, 0),  # 7
                gad7(24, 1, 1, 1, 1, 1, 1, 1),  # 7
                phq9(24, 1, 1, 1, 1, 1, 1, 0, 0, 0),  # 6
                gad7(17, 1, 1, 1, 1, 0, 1, 1),  # 6
                phq9(17, 1, 1, 1, 1, 0, 1, 0, 0, 0),  # 5
                gad7(10, 1, 1, 1, 0, 0, 1, 1),  # 5
                phq9(10, 1, 1, 1, 1, 0, 0, 0, 0, 0),  # 4
                gad7(3, 1, 1, 0, 0, 0, 1, 1),  # 4
                phq9(3, 1, 1, 0, 1, 0, 0, 0, 0, 0),  # 3
            ),
            birth_date=date(1985, 7, 22),
            sex="female",
            diagnoses=(GAD,),
            # Раздел «Приём» на демо (#34): «Авиандр» 20 дней, два пропуска вечером.
            # У Лебедевой назначения нет: по сценарию 1 врач подбирает терапию вживую.
            course=SeedCourse(days_ago=20, missed_evenings=(11, 4), unmarked_mornings=(2,)),
        ),
        # Только что зарегистрирован: пустая карточка, телефона нет.
        SeedPatient(
            "patient4@example.com",
            "Фролов Дмитрий Олегович",
            DEMO_DOCTOR,
            None,
            (),
            birth_date=date(1979, 11, 3),
            sex="male",
        ),
        # Сценарий 3: ГТР с депрессивной симптоматикой, моклобемид (ингибитор МАО) отменён
        # 10 дней назад — «Авиандр» противопоказан ещё 4 дня (шаг s5).
        SeedPatient(
            "patient6@example.com",
            "Смирнова Ольга Николаевна",
            DEMO_DOCTOR,
            "+70000000006",
            (
                gad7(1, 2, 2, 2, 2, 2, 2, 1),  # 13
                phq9(1, 2, 2, 2, 2, 2, 2, 2, 2, 0),  # 16
            ),
            birth_date=date(1974, 4, 2),
            sex="female",
            diagnoses=(GAD,),
            medications=(SeedMedication("Аурорикс", stopped_days_ago=10),),
        ),
        # Сценарий 3, шаги s8–s9: тот же профиль на 16-й день после отмены моклобемида.
        SeedPatient(
            "patient7@example.com",
            "Кузнецова Татьяна Сергеевна",
            DEMO_DOCTOR,
            "+70000000007",
            (
                gad7(1, 2, 2, 2, 2, 2, 2, 1),  # 13
                phq9(1, 2, 2, 2, 2, 2, 2, 2, 2, 0),  # 16
            ),
            birth_date=date(1974, 9, 11),
            sex="female",
            diagnoses=(GAD,),
            medications=(SeedMedication("Аурорикс", stopped_days_ago=16),),
        ),
        # Сценарий 2: тревога после COVID-19, суматриптан от мигрени по требованию. Шаг s2 —
        # GAD-7 9 и PHQ-9 6; диагнозы — показание «Авиандра» post-covid-anxiety. Назначение
        # в сид не входит: подбор и подтверждение (s5–s6) снимаются вживую.
        SeedPatient(
            "patient8@example.com",
            "Волков Игорь Петрович",
            DEMO_DOCTOR,
            "+70000000008",
            (
                gad7(1, 2, 1, 1, 2, 1, 1, 1),  # 9
                phq9(1, 1, 1, 1, 1, 1, 0, 1, 0, 0),  # 6
            ),
            birth_date=date(1980, 3, 15),
            sex="male",
            diagnoses=(
                SeedDiagnosis("F43.2", "Расстройство приспособительных реакций"),
                SeedDiagnosis("U09.9", "Состояние после COVID-19 неуточнённое"),
            ),
            medications=(SeedMedication("Суматриптан"),),
        ),
        # Ухудшение за 5 недель, в последней записи — мысли о смерти (пункт 9 PHQ-9 = 1).
        SeedPatient(
            "patient2@example.com",
            "Котов Сергей Андреевич",
            SECOND_DOCTOR,
            "+70000000002",
            (
                gad7(29, 1, 1, 1, 1, 1, 2, 1),  # 8
                phq9(29, 1, 1, 1, 2, 1, 1, 1, 1, 0),  # 9
                gad7(22, 1, 2, 1, 1, 1, 2, 1),  # 9
                phq9(22, 2, 2, 1, 2, 1, 1, 2, 1, 0),  # 12
                gad7(15, 2, 2, 1, 1, 1, 2, 1),  # 10
                phq9(15, 2, 2, 2, 2, 1, 2, 2, 1, 0),  # 14
                gad7(8, 2, 2, 2, 1, 1, 2, 1),  # 11
                phq9(8, 2, 2, 2, 2, 2, 2, 2, 1, 0),  # 15
                gad7(1, 2, 2, 2, 1, 1, 2, 1),  # 11
                phq9(1, 2, 2, 2, 2, 2, 2, 2, 1, 1),  # 16
            ),
            # Депрессивный эпизод: «Авиандра» по этому показанию нет, подбор это объяснит.
            birth_date=date(1971, 1, 9),
            sex="male",
            diagnoses=(SeedDiagnosis("F32.1", "Депрессивный эпизод средней степени"),),
        ),
        # Стабильно лёгкие симптомы, у врача 2: врач 1 её не видит.
        SeedPatient(
            "patient5@example.com",
            "Орлова Наталья Петровна",
            SECOND_DOCTOR,
            "+70000000005",
            (
                gad7(20, 1, 1, 1, 1, 1, 1, 1),  # 7
                phq9(20, 1, 1, 1, 1, 1, 1, 0, 0, 0),  # 6
                gad7(13, 1, 1, 1, 1, 0, 1, 1),  # 6
                phq9(13, 1, 1, 1, 1, 0, 1, 0, 0, 0),  # 5
                gad7(6, 1, 1, 1, 0, 0, 1, 1),  # 5
                phq9(6, 1, 1, 1, 1, 0, 0, 0, 0, 0),  # 4
            ),
            # Тревога после COVID-19: второе показание «Авиандра», курс 4 недели.
            birth_date=date(1990, 6, 30),
            sex="female",
            diagnoses=(
                SeedDiagnosis("F43.2", "Расстройство адаптации"),
                SeedDiagnosis("U09.9", "Состояние после COVID-19"),
            ),
        ),
    ),
)
