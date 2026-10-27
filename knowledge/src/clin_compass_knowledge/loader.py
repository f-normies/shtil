"""Чтение каталога `knowledge/` в проверенные объекты (спека, раздел 6)."""

import json
from collections.abc import Hashable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode

from clin_compass_knowledge.checks import MANIFEST, MANIFEST_FAILED, check
from clin_compass_knowledge.decisions import FILE as SIGNAL_DECISIONS
from clin_compass_knowledge.decisions import check_signal_decisions
from clin_compass_knowledge.issues import Issue, from_validation_error
from clin_compass_knowledge.patient_help import FILE as PATIENT_HELP
from clin_compass_knowledge.patient_help import check_patient_help
from clin_compass_knowledge.schedule import FILE as SCALE_SCHEDULE
from clin_compass_knowledge.schedule import check_scale_schedule
from clin_compass_knowledge.schemas.case import Case, CaseMeta, Expected
from clin_compass_knowledge.schemas.drug import Drug
from clin_compass_knowledge.schemas.drug_group import DrugGroup
from clin_compass_knowledge.schemas.patient_help import PatientHelp
from clin_compass_knowledge.schemas.rule import Rule
from clin_compass_knowledge.schemas.scale import Scale
from clin_compass_knowledge.schemas.scale_schedule import ScaleSchedule
from clin_compass_knowledge.schemas.scenario import Scenario
from clin_compass_knowledge.schemas.sign import Sign
from clin_compass_knowledge.schemas.signal_decisions import SignalDecisions
from clin_compass_knowledge.schemas.source import Manifest, Source

_MERGE_TAG = "tag:yaml.org,2002:merge"


class _StrictLoader(yaml.SafeLoader):
    """`SafeLoader`, который считает повтор ключа ошибкой.

    Обычный YAML молча берёт последнее значение, и опечатка в копии поля
    затирает исходное. Ключ слияния `<<:` повтором не считается.
    """

    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict[Hashable, Any]:
        seen: set[Hashable] = set()
        # Не словарь с тегом `!!map` — ошибку даст SafeLoader.
        pairs = node.value if isinstance(node, MappingNode) else []
        for key_node, _ in pairs:
            if key_node.tag == _MERGE_TAG:
                continue
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, Hashable):
                continue  # о таком ключе сообщит SafeLoader
            if key in seen:
                raise ConstructorError(None, None, f"повторяется ключ {key}", key_node.start_mark)
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def load_yaml(text: str) -> Any:
    """YAML из строки; повтор ключа — `yaml.YAMLError`. Пустой текст — None."""
    return yaml.load(text, Loader=_StrictLoader)


@dataclass(frozen=True)
class Knowledge:
    """Содержимое каталога: объекты по id."""

    root: Path
    manifest: tuple[Source, ...] = ()
    scales: Mapping[str, Scale] = field(default_factory=dict)
    rules: Mapping[str, Rule] = field(default_factory=dict)
    scenarios: Mapping[str, Scenario] = field(default_factory=dict)
    cases: Mapping[str, Case] = field(default_factory=dict)
    signs: Mapping[str, Sign] = field(default_factory=dict)
    drugs: Mapping[str, Drug] = field(default_factory=dict)
    drug_groups: Mapping[str, DrugGroup] = field(default_factory=dict)
    # Решения по сигналам; None — файла нет (мини-каталоги в тестах API), спека #93.
    signal_decisions: SignalDecisions | None = None
    # Расписание опросов пациента; None — файла нет, спека #111.
    scale_schedule: ScaleSchedule | None = None
    # Экран помощи пациенту; None — файла нет, спека #57.
    patient_help: PatientHelp | None = None
    # Файлы, о которых уже сообщено: не читаются, не прошли схему или id не совпал
    # с именем файла. Метки: `scales/<id>`, `sources/manifest`.
    # Ссылки на них не проверяются, чтобы одна ошибка не порождала десяток.
    failed: frozenset[str] = frozenset()

    @property
    def sources(self) -> dict[str, Source]:
        """Источники по id; при повторе id побеждает первый."""
        result: dict[str, Source] = {}
        for source in self.manifest:
            result.setdefault(source.id, source)
        return result


class KnowledgeError(Exception):
    """Данные не прошли проверку; `issues` — все найденные ошибки."""

    def __init__(self, issues: list[Issue]) -> None:
        self.issues = issues
        super().__init__("\n".join(str(i) for i in issues))


class _Reader:
    """Читает файлы и копит ошибки, не останавливаясь на первой."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.issues: list[Issue] = []
        self.failed: set[str] = set()

    def rel(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def report(self, path: Path, message: str) -> None:
        """Ошибка файла целиком."""
        self.issues.append(Issue(self.rel(path), "", message))

    def text(self, path: Path) -> str | None:
        """Текст файла в UTF-8; None и ошибка, если файла нет или кодировка другая."""
        try:
            return path.read_text(encoding="utf-8")
        except (FileNotFoundError, IsADirectoryError):
            self.report(path, "файла нет")
        except UnicodeDecodeError:
            self.report(path, "файл не в кодировке UTF-8")
        return None

    def raw(self, path: Path) -> Any:
        """Содержимое YAML или JSON; None и ошибка, если файл не читается или пустой."""
        text = self.text(path)
        if text is None:
            return None
        kind = "JSON" if path.suffix == ".json" else "YAML"
        try:
            data = json.loads(text) if kind == "JSON" else load_yaml(text)
        except (yaml.YAMLError, json.JSONDecodeError) as exc:
            self.report(path, f"не читается {kind}: {exc}")
            return None
        if data is None:
            self.report(path, "файл пустой")
        return data

    def model[M: BaseModel](self, path: Path, schema: type[M], data: Any = None) -> M | None:
        """Проверенный объект схемы; None, если файл не читается или не проходит схему."""
        raw = self.raw(path) if data is None else data
        if raw is None:
            return None
        try:
            return schema.model_validate(raw)
        except ValidationError as exc:
            self.issues.extend(from_validation_error(self.rel(path), exc))
            return None

    def by_id[M: Scale | Rule | Scenario | Sign | Drug | DrugGroup](
        self, folder: str, schema: type[M]
    ) -> dict[str, M]:
        """Файлы `<folder>/<id>.yaml`; id в файле должен совпадать с именем."""
        result: dict[str, M] = {}
        for path in _visible(self.root / folder):
            if path.suffix != ".yaml" or not path.is_file():
                self.report(path, "не загружается: нужен файл <id>.yaml")
                continue
            obj = self.model(path, schema)
            if obj is None:
                self.failed.add(f"{folder}/{path.stem}")
                continue
            if obj.id != path.stem:
                self.issues.append(Issue(self.rel(path), "id", "не совпадает с именем файла"))
                self.failed.add(f"{folder}/{path.stem}")
                continue
            result[obj.id] = obj
        return result

    def optional[M: BaseModel](self, relpath: str, schema: type[M]) -> M | None:
        """Необязательный файл: нет файла — None без ошибки."""
        path = self.root / relpath
        if not path.exists():
            return None
        return self.model(path, schema)

    def cases(self) -> dict[str, Case]:
        """Каталоги `cases/<id>/` с history.md, meta.yaml, expected.json."""
        result: dict[str, Case] = {}
        for case_dir in _visible(self.root / "cases"):
            if not case_dir.is_dir():
                self.report(case_dir, "не загружается: история лежит в каталоге cases/<id>/")
                continue
            history = self.text(case_dir / "history.md")
            meta = self.model(case_dir / "meta.yaml", CaseMeta)
            expected = self.model(case_dir / "expected.json", Expected)
            if meta is None or expected is None or history is None:
                continue
            case = self.model(
                case_dir,
                Case,
                {"id": case_dir.name, "history": history, "meta": meta, "expected": expected},
            )
            if case is not None:
                result[case.id] = case
        return result


def _visible(folder: Path) -> list[Path]:
    """Содержимое каталога без скрытых файлов (`.gitkeep`, `.DS_Store`)."""
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if not p.name.startswith("."))


def _read(root: Path) -> tuple[Knowledge, list[Issue]]:
    reader = _Reader(root)
    # Манифест обязателен. Без него проверка пустого или чужого каталога прошла бы молча.
    parsed = reader.model(root / MANIFEST, Manifest)
    if parsed is None:
        reader.failed.add(MANIFEST_FAILED)
    knowledge = Knowledge(
        root=root,
        manifest=parsed.sources if parsed else (),
        scales=reader.by_id("scales", Scale),
        rules=reader.by_id("rules", Rule),
        scenarios=reader.by_id("scenarios", Scenario),
        cases=reader.cases(),
        signs=reader.by_id("signs", Sign),
        drugs=reader.by_id("drugs", Drug),
        drug_groups=reader.by_id("drug-groups", DrugGroup),
        signal_decisions=reader.optional(SIGNAL_DECISIONS, SignalDecisions),
        scale_schedule=reader.optional(SCALE_SCHEDULE, ScaleSchedule),
        patient_help=reader.optional(PATIENT_HELP, PatientHelp),
        failed=frozenset(reader.failed),
    )
    return knowledge, [
        *reader.issues,
        *check(knowledge),
        *check_signal_decisions(knowledge),
        *check_scale_schedule(knowledge),
        *check_patient_help(knowledge),
    ]


def validate(root: Path) -> list[Issue]:
    """Все ошибки каталога: схемы и перекрёстные проверки."""
    _, issues = _read(root)
    return issues


def load_knowledge(root: Path) -> Knowledge:
    """Проверенное содержимое каталога. При ошибках — `KnowledgeError` со всеми ошибками."""
    knowledge, issues = _read(root)
    if issues:
        raise KnowledgeError(issues)
    return knowledge
