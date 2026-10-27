"""Снимок ГРЛС из официальной выгрузки (спека #32, разделы 5.1–5.3).

Страница поиска ГРЛС отдаёт реестр одним ZIP: по файлу xlsx на статус регистрации.
Ссылка на архив меняется с каждой выгрузкой (`FileGUID`), поэтому берётся из кнопки на странице.
Файл «Изменённый» хранит историю изменений: статус удостоверения берётся из остальных файлов,
а номер, который есть только в истории, получает статус `unknown`.

Чтение xlsx нужно только здесь: `openpyxl` ставится группой зависимостей `grls`.
"""

import csv
import datetime as dt
import html
import io
import re
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

from clin_compass_knowledge.grls import STATUS_ORDER, RegistryEntry, RegistryStatus

PAGE_URL = "https://grls.rosminzdrav.ru/GRLS.aspx"
SNAPSHOT = Path("sources/files/grls.csv")
MANIFEST = Path("sources/manifest.yaml")
_HISTORY = "Изменённый"
_FILE_STATUS: dict[str, RegistryStatus] = {
    "Действующий": "active",
    "Выдано_по_правилам_ЕАЭС": "active",
    "Действует_в_иностранных_упаковках": "active",
    "Действует_на_подтверждении_государственной_регистрации": "active",
    "Приостановлено_применение": "suspended",
    "Истёкший": "inactive",
    "Исключённый": "inactive",
}
_FILE_NAME = re.compile(r"^grls(\d{4}-\d{2}-\d{2})-\d+-(.+)\.xlsx$")
_LINK = re.compile(r"go\('(GetGRLS\.ashx\?FileGUID=[^']+)'\)")
# Начала заголовков нужных колонок; в выгрузке в заголовках бывают переводы строк.
_COLUMNS = {
    "reg_number": "Номер регистрационного удостоверения",
    "trade_name": "Торговое наименование",
    "inn": "Международное непатентованное",
}
# Браузерный User-Agent: без него сайт ГРЛС может отдать страницу-заглушку.
_USER_AGENT = "Mozilla/5.0 (compatible; clin-compass grls-update)"


@dataclass(frozen=True)
class Snapshot:
    """Строки снимка и дата выгрузки, из которой они собраны."""

    date: dt.date
    entries: list[RegistryEntry]


def download_link(page: str, page_url: str = PAGE_URL) -> str:
    """Адрес архива из кнопки «Загрузите ГРЛС одним файлом» на странице поиска."""
    found = _LINK.search(html.unescape(page))
    if found is None:
        raise ValueError("на странице нет кнопки выгрузки ГРЛС")
    return urljoin(page_url, str(found.group(1)))


def fetch_archive(page_url: str = PAGE_URL) -> bytes:
    """Скачивает текущую выгрузку: страница → ссылка из кнопки → ZIP (~20 МБ)."""
    page = _get(page_url).decode("utf-8", errors="replace")
    return _get(download_link(page, page_url))


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=300) as response:
        data: bytes = response.read()
    return data


def _reg_number(raw: object) -> str:
    """Номер удостоверения в одном написании: в истории встречается `ЛП-N (000016)`."""
    return re.sub(r"\s+", "", str(raw)).replace("N", "№")


def _rows(label: str, data: bytes) -> list[tuple[str, str, str]]:
    """(номер, торговое наименование, МНН) из листа выгрузки."""
    import openpyxl  # группа зависимостей grls: нужна только этой команде

    sheet = openpyxl.load_workbook(io.BytesIO(data), read_only=True).worksheets[0]
    columns: dict[str, int] | None = None
    result = []
    for row in sheet.iter_rows(values_only=True):
        if columns is None:
            columns = _header(row)
            continue
        reg, trade = row[columns["reg_number"]], row[columns["trade_name"]]
        # Под заголовком — строка-метка статуса без названия.
        if not reg or not trade:
            continue
        inn = str(row[columns["inn"]] or "").strip()
        # «~» в ГРЛС значит, что МНН нет (растительные сборы, комбинации).
        result.append((_reg_number(reg), str(trade).strip(), "" if inn == "~" else inn))
    if columns is None:
        raise ValueError(f"{label}: нет строки заголовка")
    return result


def _header(row: tuple[object, ...]) -> dict[str, int] | None:
    """Номера нужных колонок, если `row` — строка заголовка."""
    cells = [" ".join(str(c).split()) if c is not None else "" for c in row]
    found = {}
    for key, title in _COLUMNS.items():
        index = next((i for i, cell in enumerate(cells) if cell.startswith(title)), None)
        if index is None:
            return None
        found[key] = index
    return found


def read_archive(data: bytes) -> Snapshot:
    """Строки снимка из ZIP выгрузки, по одной на (номер, наименование, МНН)."""
    current: dict[tuple[str, str, str], RegistryStatus] = {}
    history: list[tuple[str, str, str]] = []
    dates = set()
    # Архив ГРЛС пишет имена в cp866 без флага UTF-8; имена с флагом zipfile читает как UTF-8.
    with zipfile.ZipFile(io.BytesIO(data), metadata_encoding="cp866") as archive:
        for info in archive.infolist():
            name = _FILE_NAME.match(info.filename)
            if name is None:
                raise ValueError(f"неизвестный файл выгрузки: {info.filename}")
            date, label = name.groups()
            dates.add(date)
            rows = _rows(label, archive.read(info))
            if label == _HISTORY:
                history += rows
                continue
            status = _FILE_STATUS.get(label)
            if status is None:
                raise ValueError(f"неизвестный файл выгрузки: {label}")
            for row in rows:
                current[row] = _better(current.get(row), status)
    by_number: dict[str, RegistryStatus] = {}
    for (reg, _, _), status in current.items():
        by_number[reg] = _better(by_number.get(reg), status)
    entries = {row: by_number[row[0]] for row in current}
    for row in history:
        if row[0] not in by_number:
            entries.setdefault(row, "unknown")
    if len(dates) != 1:
        raise ValueError(f"в архиве файлы разных выгрузок: {sorted(dates)}")
    ordered = sorted(entries.items(), key=lambda kv: (kv[0][1].casefold(), kv[0][0], kv[0][2]))
    return Snapshot(
        date=dt.date.fromisoformat(dates.pop()),
        entries=[RegistryEntry(reg, trade, inn, status) for (reg, trade, inn), status in ordered],
    )


def _better(current: RegistryStatus | None, new: RegistryStatus) -> RegistryStatus:
    if current is None:
        return new
    return min(current, new, key=STATUS_ORDER.index)


def write_snapshot(entries: list[RegistryEntry], path: Path) -> None:
    """CSV снимка: строки в порядке `entries`, переводы строк `\\n` для читаемого diff."""
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["reg_number", "trade_name", "inn", "status"])
        writer.writerows((e.reg_number, e.trade_name, e.inn, e.status) for e in entries)


def set_manifest_date(text: str, date: dt.date) -> str:
    """Манифест с новой датой выгрузки в записи `grls`. Комментарии и порядок полей не трогаются."""
    block = re.search(r"^  - id: grls\n(?:(?!  - ).*\n)*", text, flags=re.MULTILINE)
    if block is None:
        raise ValueError("в манифесте нет записи id: grls")
    body = block.group(0)
    body = re.sub(r"^    version: .*$", f'    version: "{date.isoformat()}"', body, flags=re.M)
    body = re.sub(r"^    date: .*$", f"    date: {date.isoformat()}", body, flags=re.M)
    return text[: block.start()] + body + text[block.end() :]


def update(root: Path, archive: bytes) -> Snapshot:
    """Пишет снимок в `root/sources/files/grls.csv` и дату выгрузки в манифест."""
    snapshot = read_archive(archive)
    write_snapshot(snapshot.entries, root / SNAPSHOT)
    manifest = root / MANIFEST
    text = manifest.read_text(encoding="utf-8")
    manifest.write_text(set_manifest_date(text, snapshot.date), encoding="utf-8")
    return snapshot
