"""Административные команды: python -m clin_compass.cli <команда>.

`create-user` создаёт врача или администратора, `seed` — синтетических врачей, пациентов
и историю шкал, `index` индексирует базу знаний для RAG. `knowledge-export` и
`knowledge-import` переносят проверенный индекс в другую базу, например на стенд
(`docs/runbooks/knowledge-index.md`).

Пароль берётся из COMPASS_NEW_USER_PASSWORD (для seed — COMPASS_SEED_PASSWORD) или
запрашивается в терминале дважды, чтобы не оставаться в истории shell.
"""

import argparse
import asyncio
import getpass
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from clin_compass_knowledge.loader import KnowledgeError, load_knowledge
from pydantic import EmailStr, TypeAdapter, ValidationError

from clin_compass.core.ai.nodes import AiNotConfiguredError
from clin_compass.core.config import Settings
from clin_compass.core.db import make_engine, make_sessionmaker
from clin_compass.modules.alerts.deps import RuleBook, repository_knowledge_dir
from clin_compass.modules.assessments.service import ScaleCatalog
from clin_compass.modules.identity import service as identity
from clin_compass.modules.identity.schemas import Role
from clin_compass.modules.knowledge import service as knowledge_service
from clin_compass.modules.therapy.deps import Catalog
from clin_compass.seed import run as seed_run
from clin_compass.seed.data import PLAN

MIN_PASSWORD_LENGTH = 8
_EMAIL = TypeAdapter(EmailStr)


def _email(email: str) -> str:
    """Та же проверка, что при входе: иначе пользователь не сможет войти никогда."""
    try:
        return _EMAIL.validate_python(email)
    except ValidationError:
        raise SystemExit(f"Некорректный email: {email}") from None


def _password(variable: str = "COMPASS_NEW_USER_PASSWORD", prompt: str = "Пароль") -> str:
    """Пароль из переменной `variable` или из терминала; в терминале — дважды, с проверкой."""
    password = os.environ.get(variable)
    if not password:
        password = getpass.getpass(f"{prompt}: ")
        # Опечатку в скрытом вводе не увидеть, а пользователь с ней не войдёт.
        if getpass.getpass("Повторите пароль: ") != password:
            raise SystemExit("Пароли не совпадают")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise SystemExit(f"Пароль должен быть не короче {MIN_PASSWORD_LENGTH} символов")
    return password


async def create_user(settings: Settings, *, email: str, role: str, full_name: str) -> None:
    email = _email(email)
    password = _password()
    engine = make_engine(settings.database_url)
    try:
        async with make_sessionmaker(engine)() as session:
            user = await identity.create_user(
                session, email=email, password=password, role=Role(role), full_name=full_name
            )
            await session.commit()
    finally:
        await engine.dispose()
    print(f"Создан пользователь {user.email} ({user.role}), id {user.id}")  # noqa: T201


async def seed(settings: Settings) -> None:
    """Синтетические врачи, пациенты и история шкал (#19). Шкалы и правила — как при старте api."""
    knowledge = load_knowledge(settings.knowledge_dir or repository_knowledge_dir())
    password = _password("COMPASS_SEED_PASSWORD", "Пароль пользователей сида")
    rules = RuleBook(knowledge.rules.values(), knowledge.sources)
    engine = make_engine(settings.database_url)
    try:
        report = await seed_run.seed(
            make_sessionmaker(engine),
            catalog=ScaleCatalog.from_knowledge(knowledge),
            rules=rules,
            plan=PLAN,
            password=password,
            now=datetime.now(UTC),
            drugs=Catalog.from_knowledge(knowledge),
        )
    except seed_run.SeedError as error:
        raise SystemExit(f"Сид остановлен: {error}") from None
    finally:
        await engine.dispose()
    summary = _seed_summary(
        report, rules_reviewed=len(rules.rules), rules_total=len(knowledge.rules)
    )
    print(summary)  # noqa: T201


def _seed_summary(report: seed_run.Report, *, rules_reviewed: int, rules_total: int) -> str:
    lines = [
        f"Врачи: создано {report.doctors_created}, уже были {report.doctors_existing}",
        f"Пациенты: создано {report.patients_created}, уже были {report.patients_existing}",
        f"Записи шкал: создано {report.records_created}, уже были {report.records_existing}, "
        f"расходятся с базой {report.records_conflict}, пропущено {report.records_skipped}",
    ]
    if report.records_conflict:
        lines.append(
            "  расходятся с базой: ключ занят записью с другими данными или датой; "
            "чтобы применить изменения, пересоздайте базу"
        )
    lines += [f"  шкала {scale}: {reason}" for scale, reason in sorted(report.skipped.items())]
    # Черновик правила сигналов не создаёт: без этой строки отсутствие сигналов не объяснить.
    lines.append(
        f"Правила сигналов: проверено {rules_reviewed} из {rules_total}; "
        "черновики (draft) сигналов не создают"
    )
    lines.append("Вход (пароль задаётся при создании; у бывших ранее пользователей он прежний):")
    lines += [f"  врач {d.email} — {d.full_name}" for d in PLAN.doctors]
    lines += [f"  пациент {p.email} — {p.full_name}" for p in PLAN.patients]
    return "\n".join(lines)


async def index(settings: Settings, *, force: bool) -> int:
    """Индексирует базу знаний для RAG; печатает строку на источник (спека #27, раздел 5).

    Returns:
        Код возврата: 1, если хоть один источник не проиндексирован.
    """
    engine = make_engine(settings.database_url)
    try:
        reports = await knowledge_service.index_sources(
            settings, make_sessionmaker(engine), force=force
        )
    except (AiNotConfiguredError, KnowledgeError) as exc:
        print(f"ошибка: {exc}")  # noqa: T201
        return 1
    finally:
        await engine.dispose()
    for report in reports:
        print(report.line())  # noqa: T201
    return 1 if any(report.status == "error" for report in reports) else 0


async def knowledge_export(settings: Settings, *, out: Path) -> int:
    """Выгружает действующие документы индекса в файл `out`; печатает версию и счётчики.

    Вывод structlog идёт в stdout, поэтому выгрузка пишется только в файл.

    Returns:
        Код возврата: 1, если в базе нет действующих документов; файл тогда не пишется.
    """
    engine = make_engine(settings.database_url)
    try:
        dump = await knowledge_service.export_index(make_sessionmaker(engine))
    except knowledge_service.TransferError as exc:
        print(f"ошибка: {exc}")  # noqa: T201
        return 1
    finally:
        await engine.dispose()
    await asyncio.to_thread(out.write_text, dump.model_dump_json(), encoding="utf-8")
    summary = dump.summary()
    print(  # noqa: T201
        f"Версия индекса {summary.version}: документов {summary.documents},"
        f" фрагментов {summary.fragments}"
    )
    return 0


async def knowledge_import(settings: Settings, *, path: str) -> int:
    """Загружает выгрузку из файла или стандартного ввода (`-`); строка на источник.

    Returns:
        Код возврата: 1 при отказе, база тогда не меняется.
    """
    try:
        data = await asyncio.to_thread(_read_dump, path)
    except OSError as exc:
        print(f"ошибка: файл не прочитан: {exc}")  # noqa: T201
        return 1
    engine = make_engine(settings.database_url)
    try:
        reports = await knowledge_service.import_index(make_sessionmaker(engine), data)
    except knowledge_service.TransferError as exc:
        print(f"ошибка: {exc}")  # noqa: T201
        return 1
    finally:
        await engine.dispose()
    for report in reports:
        print(report.line())  # noqa: T201
    return 0


def _read_dump(path: str) -> bytes:
    return sys.stdin.buffer.read() if path == "-" else Path(path).read_bytes()


def main() -> None:
    parser = argparse.ArgumentParser(prog="clin_compass.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-user", help="Создать врача или администратора")
    create.add_argument("--email", required=True)
    create.add_argument("--role", choices=[r.value for r in Role], default=Role.DOCTOR.value)
    create.add_argument("--name", required=True, help="ФИО")
    commands.add_parser("seed", help="Синтетические врачи, пациенты и история шкал")
    index_command = commands.add_parser("index", help="Индексировать базу знаний для RAG")
    index_command.add_argument(
        "--force", action="store_true", help="Переиндексировать и документы без изменений"
    )
    export = commands.add_parser(
        "knowledge-export", help="Выгрузить действующие документы индекса базы знаний в файл"
    )
    export.add_argument("--out", required=True, type=Path, help="Путь файла выгрузки")
    load = commands.add_parser(
        "knowledge-import", help="Загрузить выгрузку индекса базы знаний одной транзакцией"
    )
    load.add_argument("path", help="Файл выгрузки; - — читать стандартный ввод")
    args = parser.parse_args()

    if args.command == "create-user":
        asyncio.run(create_user(Settings(), email=args.email, role=args.role, full_name=args.name))
    elif args.command == "seed":
        asyncio.run(seed(Settings()))
    elif args.command == "index":
        raise SystemExit(asyncio.run(index(Settings(), force=args.force)))
    elif args.command == "knowledge-export":
        raise SystemExit(asyncio.run(knowledge_export(Settings(), out=args.out)))
    elif args.command == "knowledge-import":
        raise SystemExit(asyncio.run(knowledge_import(Settings(), path=args.path)))


if __name__ == "__main__":
    main()
