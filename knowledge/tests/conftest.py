"""Общие фикстуры: копия хорошего набора примеров, которую тест может испортить."""

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from clin_compass_knowledge.loader import load_yaml
from tests.helpers import VALID, Edit


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """Копия `fixtures/valid` во временном каталоге."""
    root = tmp_path / "knowledge"
    shutil.copytree(VALID, root)
    return root


@pytest.fixture
def edit(tree: Path) -> Edit:
    """Меняет YAML-файл копии: `edit("rules/x.yaml", lambda d: d.update(...))`."""

    def _edit(relpath: str, change: Callable[[Any], None]) -> None:
        path = tree / relpath
        data = load_yaml(path.read_text(encoding="utf-8"))
        change(data)
        path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    return _edit
