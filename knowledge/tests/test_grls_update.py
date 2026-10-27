"""Сборка снимка ГРЛС из официальной выгрузки (спека #32, разделы 5.1–5.3).

Архив собирается в тесте: те же имена файлов и та же раскладка листа, что в выгрузке
от 16.10.2026 (заголовок в пятой строке, строка-метка статуса под ним). Названия выдуманные,
кроме «Авиандра».
"""

import datetime as dt
import io
import zipfile
from pathlib import Path

import openpyxl
import pytest

from clin_compass_knowledge.__main__ import main
from clin_compass_knowledge.grls import RegistryEntry, load_registry
from clin_compass_knowledge.grls_update import (
    download_link,
    read_archive,
    set_manifest_date,
)

HEADER = [
    None,
    None,
    "Номер регистрационного удостоверения",
    "Дата регистрации",
    "Дата окончания действия регистрационного удостоверения",
    "Дата аннулирования регистрацион- ного удостоверения",
    "Юридическое лицо, на имя которого выдано регистрационное удостоверение",
    None,
    "Торговое наименование\nлекарственного препарата",
    "Международное непатентованное или химическое наименование",
    "Формы выпуска",
]


def _sheet(label: str, rows: list[tuple[str, str, str]]) -> bytes:
    book = openpyxl.Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.append([None] * 4)
    sheet.append([None] * 4)
    sheet.append([None, None, None, "Государственный реестр лекарственных средств"])
    sheet.append([None] * 4)
    sheet.append(HEADER)
    sheet.append([None, None, label])
    for reg, trade, inn in rows:
        sheet.append([None, None, reg, "01.01.2020", None, None, "Держатель", "Россия", trade, inn])
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def _archive(files: dict[str, list[tuple[str, str, str]]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for label, rows in files.items():
            archive.writestr(f"grls2026-10-16-1-{label}.xlsx", _sheet(label, rows))
    return buffer.getvalue()


ARCHIVE = _archive(
    {
        "Действующий": [
            ("ЛП-№(002445)-(РГ-RU)", "АВИАНДР®", "Маритупирдин"),
            ("ЛП-№(000016)-(РГ-RU)", "Кордарин", "Тестовое вещество один"),
            ("ЛП-000020", "Двойнин", "Тестовое вещество два"),
        ],
        # Номер записан иначе, чем в «Действующем»: «N» и пробел. Это то же удостоверение.
        "Изменённый": [
            ("ЛП-N (000016)-(РГ-RU)", "Кордарин", "Тестовое вещество один"),
            ("ЛП-№(002118)-(РГ-RU)", "Историн", "Тестовое вещество три"),
        ],
        # «~» в ГРЛС — МНН нет (растительные сборы).
        "Выдано_по_правилам_ЕАЭС": [
            ("ЛП-000030", "Евразин", "Тестовое вещество четыре"),
            ("ЛП-000031", "Сборин", "~"),
        ],
        "Приостановлено_применение": [("ЛП-000040", "Приостанин", "Тестовое вещество пять")],
        # Номер есть и в «Действующем»: действующий статус побеждает.
        "Истёкший": [
            ("П N000050/01", "Старин", "Тестовое вещество шесть"),
            ("ЛП-000020", "Двойнин", "Тестовое вещество два"),
        ],
    }
)


def test_snapshot_from_archive() -> None:
    snapshot = read_archive(ARCHIVE)

    assert snapshot.date == dt.date(2026, 10, 16)
    assert snapshot.entries == [
        RegistryEntry("ЛП-№(002445)-(РГ-RU)", "АВИАНДР®", "Маритупирдин", "active"),
        RegistryEntry("ЛП-000020", "Двойнин", "Тестовое вещество два", "active"),
        RegistryEntry("ЛП-000030", "Евразин", "Тестовое вещество четыре", "active"),
        RegistryEntry("ЛП-№(002118)-(РГ-RU)", "Историн", "Тестовое вещество три", "unknown"),
        RegistryEntry("ЛП-№(000016)-(РГ-RU)", "Кордарин", "Тестовое вещество один", "active"),
        RegistryEntry("ЛП-000040", "Приостанин", "Тестовое вещество пять", "suspended"),
        RegistryEntry("ЛП-000031", "Сборин", "", "active"),
        RegistryEntry("П№000050/01", "Старин", "Тестовое вещество шесть", "inactive"),
    ]


def test_unknown_status_file_is_an_error() -> None:
    # Новый файл в выгрузке значит, что формат поменялся: молча его не пропускаем.
    archive = _archive({"Действующий": [], "Непонятный": []})

    with pytest.raises(ValueError, match="неизвестный файл выгрузки: Непонятный"):
        read_archive(archive)


def test_sheet_without_header_is_an_error() -> None:
    buffer = io.BytesIO()
    book = openpyxl.Workbook()
    book_bytes = io.BytesIO()
    book.save(book_bytes)
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("grls2026-10-16-1-Действующий.xlsx", book_bytes.getvalue())

    with pytest.raises(ValueError, match="Действующий: нет строки заголовка"):
        read_archive(buffer.getvalue())


# Фрагмент страницы поиска ГРЛС от 17.10.2026.
PAGE = (
    '<td><div class="btn_box"><button class="btn_flat" onclick="go('
    "'GetGRLS.ashx?FileGUID=b470dbf5-9ec2-4070-899a-244b6937e01b&amp;UserReq=3900235'"
    '); return false;"><i class="fa fa-file-excel-o fa-lg" title="Загрузите ГРЛС одним файлом'
    ' (ZIP 18,45 МБ от 16.10.2026 05:03:22)"></i></button></div></td>'
)


def test_download_link_from_page() -> None:
    assert download_link(PAGE) == (
        "https://grls.rosminzdrav.ru/GetGRLS.ashx"
        "?FileGUID=b470dbf5-9ec2-4070-899a-244b6937e01b&UserReq=3900235"
    )


def test_page_without_link_is_an_error() -> None:
    with pytest.raises(ValueError, match="на странице нет кнопки выгрузки ГРЛС"):
        download_link("<html><body>Технические работы</body></html>")


MANIFEST = """\
# Комментарий в начале файла остаётся.
sources:
  - id: kr-depression-2024
    version: "301_3"
    date: 2024-08-26
  # Снимок ГРЛС собирает grls-update.
  - id: grls
    title: "Государственный реестр лекарственных средств"
    version: "2026-10-01"
    date: 2026-10-01
    file: files/grls.csv
"""


def test_manifest_date_changes_only_in_grls_entry() -> None:
    updated = set_manifest_date(MANIFEST, dt.date(2026, 10, 16))

    assert updated == MANIFEST.replace(
        '    version: "2026-10-01"\n    date: 2026-10-01',
        '    version: "2026-10-16"\n    date: 2026-10-16',
    )


def test_manifest_without_grls_entry_is_an_error() -> None:
    with pytest.raises(ValueError, match="в манифесте нет записи id: grls"):
        set_manifest_date("sources:\n  - id: other\n    date: 2026-01-01\n", dt.date(2026, 10, 16))


def test_cli_writes_snapshot_and_manifest(tmp_path: Path) -> None:
    (tmp_path / "sources" / "files").mkdir(parents=True)
    (tmp_path / "sources" / "manifest.yaml").write_text(MANIFEST, encoding="utf-8")
    archive = tmp_path / "grls.zip"
    archive.write_bytes(ARCHIVE)

    code = main(["grls-update", str(tmp_path), "--archive", str(archive)])

    assert code == 0
    registry = load_registry(tmp_path / "sources" / "files" / "grls.csv")
    assert registry.lookup("Историн").check == "unknown"
    assert registry.lookup("Двойнин").check == "registered"
    manifest = (tmp_path / "sources" / "manifest.yaml").read_text(encoding="utf-8")
    assert "    date: 2026-10-16\n" in manifest
