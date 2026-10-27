"""Запись данных сида через публичные сервисы модулей (спека #19, раздел 4)."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta, timezone
from uuid import UUID, uuid5

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass.modules.adherence import service as adherence
from clin_compass.modules.adherence.schemas import PastMarkIn
from clin_compass.modules.alerts.deps import RuleBook
from clin_compass.modules.assessments import service as assessments
from clin_compass.modules.assessments.schemas import AssessmentCreateIn
from clin_compass.modules.assessments.service import AssessmentError, ScaleCatalog
from clin_compass.modules.identity import service as identity
from clin_compass.modules.identity.schemas import Role
from clin_compass.modules.patients import service as patients
from clin_compass.modules.patients.schemas import DiagnosisIn, ProfileUpdate
from clin_compass.modules.therapy import service as therapy
from clin_compass.modules.therapy.deps import Catalog
from clin_compass.modules.therapy.schemas import MedicationIn
from clin_compass.seed.data import Plan, Record, SeedCourse, SeedDoctor, SeedPatient

# Москва без перехода на летнее время с 2014 года: смещения хватает, база часовых поясов
# в образе не нужна.
_MOSCOW = timezone(timedelta(hours=3))
_NO_DRUGS = Catalog(drugs={}, registries={}, sources={})
_FILLED_AT = time(10, 0)
# Отметки приёма сида: утренняя доза и вечерняя отметка дня (спека #34, раздел 8).
_MORNING = time(8, 40)
_EVENING = time(21, 5)
# Пространство имён ключей отправки сида; постоянное, иначе повторный запуск продублирует записи.
_SUBMISSIONS = UUID("7f72cd0c-707a-4995-b7e8-97a4e027b01b")
# Приглашение используется сразу же, в той же транзакции.
_INVITE_TTL = timedelta(minutes=5)


class SeedError(Exception):
    """Данные в базе противоречат плану сида."""


@dataclass
class Report:
    """Что сделал запуск. `skipped` — недоступные шкалы и причина.

    `records_conflict` — ключ записи занят записью с другими ответами или датой: план изменили
    или сид запущен в другой день. Запись в базе остаётся прежней.
    """

    doctors_created: int = 0
    doctors_existing: int = 0
    patients_created: int = 0
    patients_existing: int = 0
    records_created: int = 0
    records_existing: int = 0
    records_conflict: int = 0
    records_skipped: int = 0
    skipped: dict[str, str] = field(default_factory=dict)


def completed_at(now: datetime, days_ago: int) -> datetime:
    """10:00 по Москве в день, который был `days_ago` дней назад по московскому календарю."""
    day = now.astimezone(_MOSCOW).date() - timedelta(days=days_ago)
    return datetime.combine(day, _FILLED_AT, _MOSCOW).astimezone(UTC)


def course_marks(course: SeedCourse, times_per_day: int, now: datetime) -> list[PastMarkIn]:
    """Отметки доз назначения сида: утро — первый приём, вечер — последний.

    Будущих отметок нет: сегодня отмечено только утро, и только если 08:40 уже прошло.
    """
    today = now.astimezone(_MOSCOW).date()
    marks = []
    for days_ago in range(course.days_ago, -1, -1):
        day = today - timedelta(days=days_ago)
        for slot in range(times_per_day):
            evening = slot == times_per_day - 1 and slot > 0
            if evening and days_ago == 0:
                continue
            if slot == 0 and days_ago in course.unmarked_mornings:
                continue
            at = datetime.combine(day, _EVENING if evening else _MORNING, _MOSCOW)
            if at > now:
                continue
            missed = evening and days_ago in course.missed_evenings
            marks.append(
                PastMarkIn(day=day, slot=slot, status="missed" if missed else "taken", marked_at=at)
            )
    return marks


def skip_reason(catalog: ScaleCatalog, scale_id: str) -> str | None:
    """Почему записи шкалы не отправить. `None` — шкала выдаётся пациенту."""
    if catalog.get_available(scale_id) is not None:
        return None
    scale = catalog.scales.get(scale_id)
    if scale is None:
        return "нет в knowledge/"
    if not scale.license.allowed:
        return "лицензия не разрешает выдачу пациенту"
    if scale.status != "reviewed":
        return f"статус {scale.status}, нужен reviewed"
    if scale.respondent != "patient":
        return "шкалу заполняет врач"
    return "не выдаётся пациенту"


async def seed(
    maker: async_sessionmaker[AsyncSession],
    *,
    catalog: ScaleCatalog,
    rules: RuleBook,
    plan: Plan,
    password: str,
    now: datetime,
    drugs: Catalog | None = None,
) -> Report:
    """Создать врачей, пациентов и записи шкал, которых ещё нет. Даты — от `now`.

    `drugs` — справочник для сверки препаратов пациента с ГРЛС и для справки по КР;
    без него МНН не определяется, а справка по КР не ставится в расчёт.

    Врачи пишутся одной транзакцией, каждый пациент со своими записями — своей.
    """
    report = Report()
    async with maker() as session:
        doctors = {d.email: await _doctor(session, d, password, report) for d in plan.doctors}
        await session.commit()
    for patient in plan.patients:
        doctor_id = doctors[patient.doctor_email]
        async with maker() as session:
            patient_id = await _patient(session, patient, doctor_id, password, now, report)
            await _medications(session, drugs or _NO_DRUGS, patient, patient_id, doctors, now)
            for record in sorted(patient.records, key=lambda r: -r.days_ago):
                await _record(session, catalog, rules, patient, patient_id, record, now, report)
            if drugs is not None:
                await _guidance(session, drugs, patient_id, doctor_id, now)
                await _course(session, drugs, patient, patient_id, doctor_id, now)
            await session.commit()
    return report


async def _guidance(
    session: AsyncSession, drugs: Catalog, patient_id: UUID, doctor_id: UUID, now: datetime
) -> None:
    """Предрасчёт справки по КР для демо (спека #107, решения 16 и 18).

    Тот же запрос, что после правки диагнозов. Задачи ждут Мак в очереди; повторный запуск
    новых задач не ставит, а справку, которую посчитать не удалось, возвращает в расчёт.
    """
    summary = await patients.get_patient_for_doctor(
        session, doctor_id=doctor_id, patient_id=patient_id
    )
    if summary is None:
        return
    today = now.astimezone(_MOSCOW).date()
    await therapy.request_guidance(session, drugs, patient=summary, today=today, retry_failed=True)


async def _course(
    session: AsyncSession,
    drugs: Catalog,
    patient: SeedPatient,
    patient_id: UUID,
    doctor_id: UUID,
    now: datetime,
) -> None:
    """Назначение и отметки доз — один раз (спека #34, раздел 8)."""
    if patient.course is None:
        return
    summary = await patients.get_patient_for_doctor(
        session, doctor_id=doctor_id, patient_id=patient_id
    )
    if summary is None:
        return
    today = now.astimezone(_MOSCOW).date()
    prescription_id = await therapy.seed_prescription(
        session,
        drugs,
        patient=summary,
        started_at=now - timedelta(days=patient.course.days_ago),
        today=today,
    )
    if prescription_id is None:
        raise SeedError(f"{patient.email}: подбор не предложил назначение")
    [prescription] = [
        p
        for p in await therapy.list_prescriptions(session, patient_id, today)
        if p.id == prescription_id
    ]
    marks = course_marks(patient.course, prescription.times_per_day, now)
    await adherence.add_past_marks(session, patient_id, prescription_id, marks)


async def _medications(
    session: AsyncSession,
    drugs: Catalog,
    patient: SeedPatient,
    patient_id: UUID,
    doctors: dict[str, UUID],
    now: datetime,
) -> None:
    """Препараты вне системы — один раз, пока у пациента их нет. Даты отмены — от `now`."""
    if not patient.medications or await therapy.list_medications(session, patient_id):
        return
    today = now.astimezone(_MOSCOW).date()
    for m in patient.medications:
        stopped = None if m.stopped_days_ago is None else today - timedelta(days=m.stopped_days_ago)
        await therapy.add_medication(
            session,
            drugs,
            patient_id,
            MedicationIn(name=m.name, stopped_on=stopped),
            doctor_id=doctors[patient.doctor_email],
        )


async def _doctor(session: AsyncSession, doctor: SeedDoctor, password: str, report: Report) -> UUID:
    found = await _existing(session, doctor.email, Role.DOCTOR)
    if found is not None:
        report.doctors_existing += 1
        return found
    user = await identity.create_user(
        session, email=doctor.email, password=password, role=Role.DOCTOR, full_name=doctor.full_name
    )
    report.doctors_created += 1
    return user.id


async def _patient(
    session: AsyncSession,
    patient: SeedPatient,
    doctor_id: UUID,
    password: str,
    now: datetime,
    report: Report,
) -> UUID:
    patient_id = await _existing(session, patient.email, Role.PATIENT)
    if patient_id is None:
        invite = await patients.create_invite(session, doctor_id, _INVITE_TTL)
        user = await patients.register_patient(
            session,
            invite_token=invite.token,
            email=patient.email,
            password=password,
            full_name=patient.full_name,
            consent_version=patients.get_consent_document().version,
            registered_at=_registered_at(patient, now),
        )
        patient_id = user.id
        report.patients_created += 1
    else:
        report.patients_existing += 1
    summary = await patients.get_patient_for_doctor(
        session, doctor_id=doctor_id, patient_id=patient_id
    )
    if summary is None:
        raise SeedError(f"{patient.email}: пациент не привязан к {patient.doctor_email}")
    if patient.phone is not None and summary.phone is None:
        summary = await patients.set_phone(session, summary, patient.phone)
    if (patient.birth_date or patient.sex) and summary.birth_date is None and summary.sex is None:
        await patients.set_profile(
            session, summary, ProfileUpdate(birth_date=patient.birth_date, sex=patient.sex)
        )
    if patient.diagnoses and not await patients.list_diagnoses(session, patient_id):
        for diagnosis in patient.diagnoses:
            await patients.add_diagnosis(
                session,
                patient_id,
                DiagnosisIn(icd10=diagnosis.icd10, title=diagnosis.title),
                doctor_id=doctor_id,
            )
    return patient_id


async def _record(
    session: AsyncSession,
    catalog: ScaleCatalog,
    rules: RuleBook,
    patient: SeedPatient,
    patient_id: UUID,
    record: Record,
    now: datetime,
    report: Report,
) -> None:
    """Отправить запись так же, как её отправляет PWA: баллы и правила считает assessments."""
    reason = skip_reason(catalog, record.scale_id)
    if reason is not None:
        report.skipped[record.scale_id] = reason
        report.records_skipped += 1
        return
    scale = catalog.scales[record.scale_id]
    where = f"{patient.email}, {record.scale_id}, {record.days_ago} дн. назад"
    if len(record.answers) != len(scale.items):
        raise SeedError(f"{where}: ответов {len(record.answers)}, пунктов {len(scale.items)}")
    body = AssessmentCreateIn(
        patient_id=patient_id,
        # Один ключ на запись плана: повторный запуск не создаёт её снова.
        submission_id=uuid5(_SUBMISSIONS, f"{patient.email}:{record.scale_id}:{record.days_ago}"),
        scale_id=scale.id,
        scale_version=scale.version,
        completed_at=completed_at(now, record.days_ago),
        answers={item.id: answer for item, answer in zip(scale.items, record.answers, strict=True)},
    )
    try:
        _, created = await assessments.create_result(
            session, catalog, rules, patient_id=patient_id, body=body
        )
    except AssessmentError as error:
        # Тот же ключ с другими ответами или датой: план изменили или запуск в другой день.
        if error.code != "submission_conflict":
            raise SeedError(f"{where}: {error.detail}") from error
        report.records_conflict += 1
        return
    if created:
        report.records_created += 1
    else:
        report.records_existing += 1


async def _existing(session: AsyncSession, email: str, role: Role) -> UUID | None:
    user = await identity.get_user_by_email(session, email)
    if user is None:
        return None
    if user.role != role:
        raise SeedError(f"{email}: в базе уже есть пользователь с ролью {user.role.value}")
    return user.id


def _registered_at(patient: SeedPatient, now: datetime) -> datetime | None:
    """За день до первой записи: в карточке наблюдение начинается раньше графика."""
    if not patient.records:
        return None
    first = max(r.days_ago for r in patient.records)
    return completed_at(now, first) - timedelta(days=1)
