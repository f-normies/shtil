"""Командная строка: `python -m clin_compass_knowledge validate|grls-update [путь]`."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from clin_compass_knowledge.loader import validate

# Каталог knowledge/ этого репозитория: src/clin_compass_knowledge → knowledge.
DEFAULT_ROOT = Path(__file__).resolve().parents[2]


def main(argv: Sequence[str] | None = None) -> int:
    """Проверяет каталог или обновляет снимок ГРЛС. Код выхода 1 при ошибках данных."""
    parser = argparse.ArgumentParser(prog="clin_compass_knowledge")
    sub = parser.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("validate", help="проверить данные knowledge/")
    cmd.add_argument("root", nargs="?", type=Path, default=DEFAULT_ROOT)
    grls = sub.add_parser("grls-update", help="собрать снимок ГРЛС из официальной выгрузки")
    grls.add_argument("root", nargs="?", type=Path, default=DEFAULT_ROOT)
    grls.add_argument("--archive", type=Path, help="уже скачанный ZIP выгрузки")
    args = parser.parse_args(argv)

    if args.command == "grls-update":
        return _grls_update(args.root, args.archive)
    issues = validate(args.root)
    for issue in issues:
        print(issue)  # noqa: T201
    if issues:
        print(f"ошибок: {len(issues)}")  # noqa: T201
        return 1
    print(f"{args.root}: ошибок нет")  # noqa: T201
    return 0


def _grls_update(root: Path, archive: Path | None) -> int:
    from clin_compass_knowledge import grls_update  # openpyxl ставится только для этой команды

    data = archive.read_bytes() if archive is not None else grls_update.fetch_archive()
    snapshot = grls_update.update(root, data)
    print(f"ГРЛС от {snapshot.date}: строк {len(snapshot.entries)}")  # noqa: T201
    return 0


if __name__ == "__main__":
    sys.exit(main())
