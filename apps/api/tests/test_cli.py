"""CLI: создание пользователя (публичной регистрации врачей нет), сид и перенос индекса."""

import io
import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clin_compass import cli
from clin_compass.core.config import Settings
from clin_compass.seed.run import Report
from tests.knowledge.helpers import Piece, add_document


async def test_created_doctor_can_log_in(
    client: httpx.AsyncClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COMPASS_NEW_USER_PASSWORD", "long enough password")

    await cli.create_user(
        settings, email="new.doctor@example.com", role="doctor", full_name="Новый Врач"
    )
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "new.doctor@example.com", "password": "long enough password"},
    )

    assert response.status_code == 200
    assert response.json()["role"] == "doctor"


async def test_short_password_is_refused(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COMPASS_NEW_USER_PASSWORD", "short")

    with pytest.raises(SystemExit, match="8"):
        await cli.create_user(settings, email="x@example.com", role="doctor", full_name="X")


async def test_email_that_login_would_reject_is_refused(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Вход проверяет email строже (EmailStr): такой врач не смог бы войти никогда.
    monkeypatch.setenv("COMPASS_NEW_USER_PASSWORD", "long enough password")

    with pytest.raises(SystemExit, match="email"):
        await cli.create_user(settings, email="doctor@clinic.local", role="doctor", full_name="X")


async def test_seed_command_creates_demo_doctor_who_can_log_in(
    client: httpx.AsyncClient,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Каталог knowledge/ репозитория: какие шкалы в нём проверены, тест не важно.
    monkeypatch.setenv("COMPASS_SEED_PASSWORD", "long enough password")

    await cli.seed(settings)

    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "doctor@example.com", "password": "long enough password"},
    )
    assert response.status_code == 200
    assert "doctor@example.com" in capsys.readouterr().out


def test_seed_summary_explains_records_that_differ_from_the_database() -> None:
    summary = cli._seed_summary(Report(records_conflict=2), rules_reviewed=0, rules_total=0)

    assert "расходятся с базой 2" in summary
    assert "ключ занят записью с другими данными или датой" in summary
    assert "пересоздайте базу" in summary


def test_seed_summary_has_no_conflict_hint_without_conflicts() -> None:
    summary = cli._seed_summary(Report(records_existing=3), rules_reviewed=0, rules_total=0)

    assert "пересоздайте базу" not in summary


def test_seed_summary_counts_reviewed_rules() -> None:
    # Черновик правила сигналов не создаёт: оператор должен видеть, сколько правил работает.
    summary = cli._seed_summary(Report(), rules_reviewed=1, rules_total=5)

    assert "проверено 1 из 5" in summary


def prompted(monkeypatch: pytest.MonkeyPatch, *answers: str) -> list[str]:
    """Ответы терминала на запрос пароля по очереди; возвращает показанные запросы."""
    # Makefile экспортирует .env в тесты: пароль оттуда обошёл бы запрос.
    monkeypatch.delenv("COMPASS_SEED_PASSWORD", raising=False)
    prompts: list[str] = []
    replies = iter(answers)

    def getpass(prompt: str = "") -> str:
        prompts.append(prompt)
        return next(replies)

    monkeypatch.setattr(cli.getpass, "getpass", getpass)
    return prompts


async def test_seed_refuses_password_that_was_not_repeated(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Опечатка в пароле оставила бы всех пользователей сида без входа.
    prompts = prompted(monkeypatch, "long enough password", "long enough passwrod")

    with pytest.raises(SystemExit, match="не совпадают"):
        await cli.seed(settings)
    assert prompts[0].startswith("Пароль пользователей сида")
    assert len(prompts) == 2


async def test_seed_takes_password_repeated_in_terminal(
    client: httpx.AsyncClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompted(monkeypatch, "long enough password", "long enough password")

    await cli.seed(settings)

    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "doctor@example.com", "password": "long enough password"},
    )
    assert response.status_code == 200


async def test_seed_refuses_short_password(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COMPASS_SEED_PASSWORD", "short")

    with pytest.raises(SystemExit, match="8"):
        await cli.seed(settings)


async def _one_document(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    async with sessionmaker() as session:
        await add_document(session, "kr-test", [Piece("Первый."), Piece("Второй.")])
        await session.commit()


async def test_knowledge_export_writes_file_and_prints_summary(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await _one_document(sessionmaker)
    out = tmp_path / "index.json"

    code = await cli.knowledge_export(settings, out=out)

    assert code == 0
    dump = json.loads(out.read_text(encoding="utf-8"))
    assert [d["source_id"] for d in dump["documents"]] == ["kr-test"]
    assert capsys.readouterr().out == (
        f"Версия индекса {dump['version']}: документов 1, фрагментов 2\n"
    )


async def test_knowledge_import_reads_stdin_and_prints_line_per_source(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _one_document(sessionmaker)
    out = tmp_path / "index.json"
    await cli.knowledge_export(settings, out=out)
    async with sessionmaker() as session:
        await session.execute(text("TRUNCATE knowledge.fragments, knowledge.documents"))
        await session.commit()
    capsys.readouterr()
    monkeypatch.setattr(cli.sys, "stdin", io.TextIOWrapper(io.BytesIO(out.read_bytes())))

    code = await cli.knowledge_import(settings, path="-")

    assert code == 0
    assert capsys.readouterr().out.splitlines() == ["kr-test: загружен, фрагментов: 2"]
    async with sessionmaker() as session:
        assert await session.scalar(text("SELECT count(*) FROM knowledge.fragments")) == 2


async def test_knowledge_import_refusal_exits_with_1(
    settings: Settings, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "index.json"
    bad.write_text('{"format": 1}', encoding="utf-8")

    code = await cli.knowledge_import(settings, path=str(bad))

    assert code == 1
    assert capsys.readouterr().out.startswith("ошибка: файл не по схеме")


async def test_knowledge_export_of_empty_index_is_refused(
    settings: Settings, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Пустой файл, загруженный на стенд, ничего бы не изменил, а выглядел бы как индекс.
    out = tmp_path / "index.json"

    code = await cli.knowledge_export(settings, out=out)

    assert code == 1
    assert capsys.readouterr().out == "ошибка: в базе нет действующих документов\n"
    assert not out.exists()


async def test_knowledge_import_of_file_without_documents_is_refused(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await _one_document(sessionmaker)
    out = tmp_path / "index.json"
    await cli.knowledge_export(settings, out=out)
    dump = json.loads(out.read_text(encoding="utf-8"))
    out.write_text(json.dumps({**dump, "version": None, "documents": []}), encoding="utf-8")
    capsys.readouterr()

    code = await cli.knowledge_import(settings, path=str(out))

    assert code == 1
    assert capsys.readouterr().out == "ошибка: в файле нет документов\n"
