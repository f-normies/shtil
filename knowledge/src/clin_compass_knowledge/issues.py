"""Ошибка в данных: файл, путь до поля, что не так."""

from dataclasses import dataclass

from pydantic import ValidationError
from pydantic_core import ErrorDetails

_DATE = "нужна дата в формате ГГГГ-ММ-ДД"
_INT = "нужно целое число"
_FLOAT = "нужно число"
_BOOL = "нужно true или false"
_MAPPING = "нужен набор полей «ключ: значение»"
_URL = "нужен полный адрес вида https://…"

# Ошибки pydantic по-русски. Шаблон берёт значения из `ctx` ошибки.
# Тип без шаблона остаётся с текстом pydantic.
_MESSAGES = {
    "extra_forbidden": "лишнее поле",
    "missing": "обязательное поле",
    "literal_error": "допустимые значения: {expected}",
    "string_type": "нужна строка; число или дату возьмите в кавычки",
    "string_too_short": "минимальная длина строки — {min_length}",
    "string_pattern_mismatch": "не подходит под формат {pattern}",
    "int_type": _INT,
    "int_parsing": _INT,
    "int_from_float": _INT,
    "float_type": _FLOAT,
    "float_parsing": _FLOAT,
    "bool_type": _BOOL,
    "bool_parsing": _BOOL,
    "too_short": "минимум элементов в списке — {min_length}, сейчас {actual_length}",
    "tuple_type": "нужен список",
    "model_type": _MAPPING,
    "dict_type": _MAPPING,
    "greater_than_equal": "нужно не меньше {ge}",
    "less_than_equal": "нужно не больше {le}",
    "url_parsing": _URL,
    "url_scheme": _URL,
    "date_type": _DATE,
    "date_parsing": _DATE,
    "date_from_datetime_parsing": _DATE,
    "date_from_datetime_inexact": _DATE,
}


@dataclass(frozen=True)
class Issue:
    """Одна ошибка. `path` — путь до поля в файле, пустой — ошибка файла целиком."""

    file: str
    path: str
    message: str

    def __str__(self) -> str:
        if not self.path:
            return f"{self.file}: {self.message}"
        return f"{self.file}: {self.path}: {self.message}"


def format_loc(loc: tuple[str | int, ...]) -> str:
    """`("items", 0, "txt")` → `items[0].txt`."""
    path = ""
    for part in loc:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += f".{part}" if path else part
    return path


def _message(error: ErrorDetails) -> str:
    """Текст ошибки pydantic по-русски."""
    ctx = error.get("ctx", {})
    if error["type"] == "value_error" and "error" in ctx:
        # Текст из нашего валидатора, без приставки pydantic «Value error, ».
        return str(ctx["error"])
    template = _MESSAGES.get(error["type"])
    if template is None:
        return error["msg"]
    # pydantic перечисляет варианты по-английски: 'a', 'b' or 'c'.
    values = {key: str(value).replace(" or ", " или ") for key, value in ctx.items()}
    try:
        return template.format(**values)
    except KeyError:
        return error["msg"]


def from_validation_error(file: str, error: ValidationError) -> list[Issue]:
    """Ошибки pydantic → список `Issue` для файла."""
    return [Issue(file, format_loc(tuple(e["loc"])), _message(e)) for e in error.errors()]
