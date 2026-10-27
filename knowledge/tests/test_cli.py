"""`python -m clin_compass_knowledge validate` и проверка настоящего knowledge/."""

from pathlib import Path

import pytest

from clin_compass_knowledge.__main__ import main
from clin_compass_knowledge.loader import validate
from tests.helpers import REPO_KNOWLEDGE, VALID, Edit


def test_repository_knowledge_is_valid() -> None:
    """Настоящие данные в knowledge/ проходят все проверки — тот же тест идёт в CI."""
    assert [str(i) for i in validate(REPO_KNOWLEDGE)] == []


def test_valid_tree_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["validate", str(VALID)]) == 0
    assert "ошибок нет" in capsys.readouterr().out


def test_invalid_tree_prints_issues_and_exits_one(
    tree: Path, edit: Edit, capsys: pytest.CaptureFixture[str]
) -> None:
    edit("rules/example-self-harm.yaml", lambda d: d.update(level="desirable"))

    assert main(["validate", str(tree)]) == 1
    out = capsys.readouterr().out
    assert "rules/example-self-harm.yaml: level: у alert уровень всегда mandatory" in out
    assert "ошибок: 1" in out


def test_broken_manifest_does_not_cascade(tree: Path) -> None:
    (tree / "sources/manifest.yaml").write_text("sources: 5\n", encoding="utf-8")

    issues = validate(tree)

    assert [i.file for i in issues] == ["sources/manifest.yaml"]


def test_missing_directory_exits_one(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["validate", str(tmp_path / "nonexistent")]) == 1
    assert "sources/manifest.yaml: файла нет" in capsys.readouterr().out
