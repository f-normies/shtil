"""Общие пути и функции тестов."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from clin_compass_knowledge.loader import validate

FIXTURES = Path(__file__).parent / "fixtures"
VALID = FIXTURES / "valid"
# Настоящий каталог knowledge/ этого репозитория.
REPO_KNOWLEDGE = Path(__file__).parents[1]

# Правка YAML-файла копии: `edit("rules/x.yaml", lambda d: d.update(...))`.
Edit = Callable[[str, Callable[[Any], None]], None]


def messages(root: Path) -> list[str]:
    """Все ошибки каталога строками `<файл>: <путь>: <что не так>`."""
    return [str(issue) for issue in validate(root)]
