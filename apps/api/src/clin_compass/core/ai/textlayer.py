"""Сведение вывода olmOCR 2 с текстовым слоем PDF (спека #27, 3.4).

Модель может незаметно поправить слово или цифру. Поэтому слова страницы берутся из текстового
слоя PDF, а от модели — только разметка: строки, заголовки, HTML-таблицы.

Оба текста делятся на токены: слова (буквы и цифры подряд) и отдельные знаки препинания.
Пробелы, переводы строк, HTML-теги и разметка markdown — разделители, они берутся у модели.
Токены выравниваются `difflib` по ключу: регистр, «ё», похожие латинские и кириллические буквы,
надстрочные цифры и виды тире не различаются. На место каждого токена модели встаёт токен слоя;
лишние токены модели убираются, пропущенные вставляются. Итог по токенам совпадает со слоем.
Если несведённых токенов слишком много, порядок чтения разошёлся, и разметке модели верить нельзя.
Когда модель сдвинула слова мелкими кусками (собрала ячейки нескольких строк таблицы в одну)
или пропустила слово на границе ячеек таблицы, страница берётся из слоя целиком, без разметки
модели. Длинный несведённый участок (пропущенный
абзац, перенесённая таблица), много сдвигов или много ошибок чтения останавливают документ.

Файл длиннее 400 строк: подготовка слоя, выравнивание и проверка страницы — один алгоритм
на общих токенах и ключах сравнения. LaTeX модели снимается отдельно, в `latex.py`.
"""

import html
import itertools
import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Protocol

from clin_compass.core.ai.latex import strip_latex

# Пределы подобраны по 10 страницам ОХЛП «Авиандра» и 66 страницам ОХЛП «Лирики», «Ципралекса»
# и «Афобазола» (#105). Несведённые токены двух видов. Сдвинутый — слово пропало у модели в одном
# месте и появилось в слое в другом: так выглядит перестановка ячеек, и разметке модели верить
# нельзя. Остальные — модель прочла слово иначе или пропустила его, токен слоя встаёт на место.
# На настоящих страницах сдвинутых токенов нет, кроме страницы 17 «Ципралекса» (2,5 %: модель
# собрала частоты трёх строк таблицы в одну ячейку); остальных — до 4,2 % (плотная таблица реакций
# «Лирики»). Страница со сдвигами берётся из слоя без разметки модели, если сдвинуто не больше
# `MAX_MOVED`. Доли считаются, если токенов этого вида больше `MAX_RUN`. Третий предел — самый
# длинный участок несведённых токенов подряд у модели или в слое: на настоящих страницах до
# 5 токенов, пропущенный абзац от 15 токенов даёт участок от 15, перенесённая таблица — 19.
MAX_UNMATCHED = 0.05
MAX_MOVED = 0.05
MAX_RUN = 8
_EXAMPLES = 5
_EXAMPLE_CHARS = 60

_TAGS = (
    "table|thead|tbody|tfoot|tr|td|th|caption|colgroup|col|br|p|div|span|b|i|u|em|strong"
    "|ul|ol|li|h[1-6]|img|a|hr|pre|code|blockquote|sup|sub"
)
# Индексы снимаются до сведения: «сонливость<sup>1</sup>» в слое — одно слово «сонливость1».
# LaTeX от olmOCR 2 — `latex.strip_latex`.
_INDEX_TAG = re.compile(r"</?(?:sup|sub)\b[^<>]*>", re.IGNORECASE)
# Разметка (вывод модели, сведённый текст): тег — только известный HTML-тег, поэтому «СКФ <60»
# и «≥l/10000» тегами не считаются; HTML-сущность — один токен.
_MARKUP_PIECE = re.compile(
    rf"(?P<sep>(?:</?(?:{_TAGS})\b[^<>]*>|[\s#*_|])+)"
    r"|(?P<token>&(?:[a-z]+|#\d+|#x[0-9a-f]+);|[^\W_]+|.)",
    re.IGNORECASE | re.DOTALL,
)
# Простой текст (слой PDF, текст фрагментов): ни тегов, ни сущностей.
_PLAIN_PIECE = re.compile(r"(?P<sep>[\s#*_|]+)|(?P<token>[^\W_]+|.)", re.DOTALL)
_STRUCTURE = re.compile(r"<[^<>]+>|\n")
_CELL_BORDER = re.compile(r"</?(?:td|th|tr)\b|<br\b", re.IGNORECASE)
# Граница слитых ячеек слоя — строчная буква алфавита, за ней заглавная. Знаки вроде «º» в «ºС»
# Python тоже считает строчными, но это не граница.
_LOWER = re.compile("[a-zа-яё]")
_UPPER = re.compile("[A-ZА-ЯЁ]")
# Номер страницы: «3» или колонтитул «3 из 30».
_PAGE_NUMBER = re.compile(r"\d+(?: из \d+)?")
# Слово слоя: части через 0x02. Так pdfium отдаёт знак переноса в конце строки, перевод строки
# после него он убирает.
_LAYER_WORD = re.compile(r"[^\W_]+(?:\x02[^\W_]+)*")
# Слово с большим числом мест выбора не перебираем (2^8 вариантов), берём вариант по умолчанию.
_MAX_CUTS = 8
# Математический курсив и прочие начертания Unicode: в формулах слоя «𝐿𝑐𝑟» — это «Lcr».
_MATH_LETTER = re.compile("[\U0001d400-\U0001d7ff]")
_FOLD = str.maketrans(
    {
        **dict(zip("⁰¹²³⁴⁵⁶⁷⁸⁹₀₁₂₃₄₅₆₇₈₉", "01234567890123456789", strict=True)),
        **dict.fromkeys("‐‑‒–—−", "-"),
        # Кириллические буквы, похожие на латинские (в нижнем регистре или в заглавном).
        **dict(zip("авекмнорстухіјѕ", "abekmhopctyxijs", strict=True)),
    }
)


class PageText(Protocol):
    """Страница: номер, текст модели и текстовый слой PDF (`ParsedPage` из `core/ai/docparse`)."""

    @property
    def number(self) -> int:
        """Номер страницы с единицы."""
        ...

    @property
    def text(self) -> str:
        """Текст страницы от olmOCR 2."""
        ...

    @property
    def layer_text(self) -> str:
        """Текстовый слой страницы из PDF; пустая строка — слоя нет."""
        ...


@dataclass(frozen=True)
class ReconciledPage:
    """Страница после сведения: разметка модели, слова слоя.

    `text` — HTML-разметка: знаки «&», «<» и «>» из слоя в нём экранированы.
    `layer_only` — порядок слов модели разошёлся со слоем мелкими кусками, текст страницы — слой
    без разметки модели.
    """

    number: int
    text: str
    layer_only: bool = False


@dataclass(frozen=True)
class PageMismatch:
    """Страница не сведена: доля несведённых токенов слоя и модели и примеры «модель → слой».

    `no_layer` — у страницы нет текстового слоя (скан): сводить не с чем, сканы — задача #30.
    """

    number: int
    unmatched: float
    examples: tuple[str, ...]
    no_layer: bool = False


@dataclass(frozen=True)
class _Tokens:
    """Токены текста. `gaps[i]` — разделитель перед `words[i]`, последний — хвост текста."""

    words: tuple[str, ...]
    gaps: tuple[str, ...]


def reconcile_pages(
    pages: Sequence[PageText],
) -> tuple[list[ReconciledPage], list[PageMismatch]]:
    """Свести страницы с текстовым слоем. Пустой список расхождений — все страницы сведены.

    Страница без слоя (пусто или только номер страницы) — расхождение с `no_layer=True`.
    """
    reconciled: list[ReconciledPage] = []
    mismatches: list[PageMismatch] = []
    vocabulary = _vocabulary(pages)
    for page in pages:
        if not _tokens(_layer(page.layer_text, frozenset(), vocabulary), markup=False).words:
            reconciled.append(ReconciledPage(page.number, page.text))
            mismatches.append(PageMismatch(page.number, 1.0, (), no_layer=True))
            continue
        text, mismatch, layer_only = _reconcile(page.number, page.text, page.layer_text, vocabulary)
        reconciled.append(ReconciledPage(page.number, text, layer_only))
        if mismatch is not None:
            mismatches.append(mismatch)
    return reconciled, mismatches


def _vocabulary(pages: Sequence[PageText]) -> frozenset[str]:
    """Ключи слов документа: токены модели всех страниц и цельные слова слоя.

    Слово слоя цельное, если в нём нет знака переноса и перехода строчной буквы в заглавную.
    """
    keys = {_key(w) for page in pages for w in _tokens(_model_text(page.text), markup=True).words}
    for page in pages:
        keys.update(
            _key(m.group())
            for m in _LAYER_WORD.finditer(page.layer_text)
            if len(_atoms(m.group())[0]) == 1
        )
    return frozenset(keys)


def _layer(layer_text: str, page_keys: frozenset[str], vocabulary: frozenset[str]) -> str:
    """Слой без номера страницы: строки из одного числа или «N из M» в начале или в конце.

    Такая строка в середине страницы остаётся: так в слое стоит номер сноски.
    Слова с переносами и слитыми ячейками делятся так, как их читает модель (`_segment`).
    """
    text = _MATH_LETTER.sub(lambda m: unicodedata.normalize("NFKC", m.group()), layer_text)
    text = _LAYER_WORD.sub(lambda m: _segment(m.group(), page_keys, vocabulary), text)
    lines = [line for line in text.replace("\x02", "-").splitlines() if line.strip()]
    if lines and _PAGE_NUMBER.fullmatch(lines[0].strip()):
        lines = lines[1:]
    if lines and _PAGE_NUMBER.fullmatch(lines[-1].strip()):
        lines = lines[:-1]
    return "\n".join(lines)


def _atoms(word: str) -> tuple[list[str], list[tuple[str, ...]]]:
    """Части слова слоя и места выбора между ними: варианты разделителя, первый — по умолчанию.

    Знак переноса между строчными буквами по умолчанию — перенос слова, иначе — дефис
    («ВИЧ-инфекция»). Рядом с цифрой — только дефис: склейка по чтению модели сделала бы из
    «5-10 мг» дозу «510 мг». Переход строчной буквы алфавита в заглавную по умолчанию слитный.
    """
    atoms: list[str] = [""]
    cuts: list[tuple[str, ...]] = []
    for index, char in enumerate(word):
        if char == "\x02":
            before, after = atoms[-1][-1:], word[index + 1 : index + 2]
            if not (before.isalpha() and after.isalpha()):
                cuts.append(("-",))
            elif before.islower() and after.islower():
                cuts.append(("", "-"))
            else:
                cuts.append(("-", ""))
            atoms.append("")
            continue
        if _LOWER.fullmatch(atoms[-1][-1:]) and _UPPER.fullmatch(char):
            cuts.append(("", " "))
            atoms.append("")
        atoms[-1] += char
    return atoms, cuts


def _segment(word: str, page_keys: frozenset[str], vocabulary: frozenset[str]) -> str:
    """Слово слоя так, как его читает модель. Буквы слоя не меняются.

    Слой не различает два случая, их видно только на странице. Знак переноса 0x02 стоит и при
    переносе слова («кор-пусом»), и после дефиса в конце строки («COVID-19»). Соседние ячейки
    таблицы pdfium отдаёт без пробела («нарушенияСостояние»). Места выбора — знак переноса
    (слитно или дефис) и переход строчной буквы в заглавную (слитно или пробел).

    Выбирается вариант, все слова которого есть у модели на этой странице (`page_keys`), затем —
    в словах документа (`vocabulary`); при нескольких таких — с большим числом букв. Иначе —
    вариант по умолчанию (`_atoms`).
    """
    atoms, cuts = _atoms(word)
    if not cuts:
        return word
    if len(cuts) > _MAX_CUTS:
        cuts = [cut[:1] for cut in cuts]
    variants = [
        [p for pair in zip(atoms, (*choice, ""), strict=True) for p in pair]
        for choice in itertools.product(*cuts)
    ]
    best = variants[0]
    best_score = (_covered(best, page_keys), _covered(best, vocabulary))
    for variant in variants[1:]:
        score = (_covered(variant, page_keys), _covered(variant, vocabulary))
        if score > best_score:
            best, best_score = variant, score
    return "".join(best)


def _covered(variant: Sequence[str], keys: frozenset[str]) -> int:
    """Буквы слов варианта, которые есть в `keys`.

    `variant` — части и разделители по очереди: часть, разделитель, часть, …, пустой хвост.
    Части через пробел — разные ячейки таблицы, они засчитываются по отдельности. Части через
    дефис — одно слово, оно засчитывается целиком: «не-до-статочность» не подходит, даже если
    «не» и «до» — слова.
    """
    total = 0
    for cell in "".join(variant).split(" "):
        words = cell.split("-")
        total += sum(map(len, words)) if all(_key(w) in keys for w in words) else 0
    return total


def _model_text(text: str) -> str:
    """Текст модели для сведения: без тегов индексов и без LaTeX."""
    return strip_latex(_INDEX_TAG.sub("", text))


def words(text: str, *, markup: bool) -> list[str]:
    """Слова и знаки текста по порядку, без разделителей; сущности разметки раскрыты.

    `markup=True` — текст с HTML-тегами и сущностями (вывод модели, сведённая страница),
    `markup=False` — простой текст (слой PDF, текст фрагмента).
    """
    tokens = _tokens(text, markup=markup).words
    return [html.unescape(t) if markup and _is_entity(t) else t for t in tokens]


def _tokens(text: str, *, markup: bool) -> _Tokens:
    words: list[str] = []
    gaps: list[str] = []
    gap = ""
    for piece in (_MARKUP_PIECE if markup else _PLAIN_PIECE).finditer(text):
        if piece.group("sep") is not None:
            gap += piece.group("sep")
            continue
        gaps.append(gap)
        words.append(piece.group("token"))
        gap = ""
    gaps.append(gap)
    return _Tokens(tuple(words), tuple(gaps))


def _key(token: str) -> str:
    """Ключ сравнения: регистр, «ё», похожие буквы, индексы, тире и HTML-сущности не важны."""
    if _is_entity(token):
        token = html.unescape(token)
    return token.lower().replace("ё", "е").translate(_FOLD)


def _reconcile(
    number: int, text: str, layer_text: str, vocabulary: frozenset[str]
) -> tuple[str, PageMismatch | None, bool]:
    """Текст страницы, расхождение, если страницу свести нельзя, и признак текста из слоя."""
    model = _tokens(_model_text(text), markup=True)
    model_keys = [_key(w) for w in model.words]
    layer_text = _layer(layer_text, frozenset(model_keys), vocabulary)
    layer = _tokens(layer_text, markup=False)
    matcher = SequenceMatcher(None, model_keys, [_key(w) for w in layer.words], autojunk=False)
    opcodes = matcher.get_opcodes()
    result = _rebuild(model, layer, opcodes)
    differ = [op for op in opcodes if op[0] != "equal"]
    total = max(1, len(model.words) + len(layer.words))
    unmatched = sum(i2 - i1 + j2 - j1 for _, i1, i2, j1, j2 in differ)
    moved = _moved(model_keys, [_key(w) for w in layer.words], differ)
    share = unmatched / total
    # Модель путает отдельные слова. Длинный участок подряд — пропущенный или переставленный
    # абзац: на длинной странице его доля мала, но разметке модели здесь верить нельзя.
    run = max((max(i2 - i1, j2 - j1) for _, i1, i2, j1, j2 in differ), default=0)
    # На короткой странице одно слово — уже несколько процентов: доля без нижней границы
    # остановила бы документ из-за одной ошибки, которую сведение и так исправило.
    misread = (unmatched - moved) / total > MAX_UNMATCHED and unmatched - moved > MAX_RUN
    too_moved = moved / total > MAX_MOVED and moved > MAX_RUN
    # Итог по токенам обязан совпасть со слоем; иначе сведение сломалось, и верить ему нельзя.
    rebuilt = words(result, markup=True) == list(layer.words)
    if run <= MAX_RUN and rebuilt and not misread and not too_moved:
        if not moved and not _at_cell_border(model, differ):
            return result, None, False
        # Разметка модели поставила бы слова не в те ячейки, а слой их порядок знает.
        return html.escape(layer_text, quote=False), None, True
    largest = sorted(differ, key=lambda op: op[2] - op[1] + op[4] - op[3], reverse=True)
    examples = tuple(
        f"{_phrase(model, i1, i2)} → {_phrase(layer, j1, j2)}"
        for _, i1, i2, j1, j2 in sorted(largest[:_EXAMPLES], key=lambda op: op[1])
    )
    return result, PageMismatch(number, share, examples), False


def _moved(
    model_keys: Sequence[str],
    layer_keys: Sequence[str],
    differ: Sequence[tuple[str, int, int, int, int]],
) -> int:
    """Сдвинутые токены: слово пропало у модели в одном месте и есть в слое в другом.

    Считаются пары одинаковых слов среди несведённых токенов модели и слоя. Ошибка чтения так
    не выглядит: слова модели «конъюнктивит» в слое нет совсем.
    """
    gone = Counter(k for _, i1, i2, _, _ in differ for k in model_keys[i1:i2] if _is_word(k[0]))
    came = Counter(k for _, _, _, j1, j2 in differ for k in layer_keys[j1:j2] if _is_word(k[0]))
    return 2 * sum((gone & came).values())


def _at_cell_border(model: _Tokens, differ: Sequence[tuple[str, int, int, int, int]]) -> bool:
    """Слова слоя, которых нет у модели, встают на границу ячеек таблицы или строк.

    `_rebuild` ставит такие слова в конец предыдущей ячейки. Если модель пропустила слово
    в начале ячейки, оно ушло бы в чужую ячейку: так частота реакции попадает к соседней строке.
    То же для замены, у которой слов у модели и в слое разное число, а внутри — граница ячеек.
    """
    for tag, i1, i2, j1, j2 in differ:
        if tag == "insert" and _CELL_BORDER.search(model.gaps[i1]):
            return True
        unequal = tag == "replace" and i2 - i1 != j2 - j1
        if unequal and any(_CELL_BORDER.search(model.gaps[i]) for i in range(i1 + 1, i2 + 1)):
            return True
    return False


def _rebuild(
    model: _Tokens, layer: _Tokens, opcodes: Sequence[tuple[str, int, int, int, int]]
) -> str:
    """Разделители модели и токены слоя по опкодам выравнивания."""
    out: list[str] = []
    first_gap_used = False

    def emit(gap: str, token: str = "") -> None:
        if gap:
            out.append(gap)
        if not token:
            return
        # Два слова подряд без разделителя слились бы в одно.
        if out and _is_word(out[-1][-1]) and _is_word(token[0]):
            out.append(" ")
        # Итог — HTML-разметка: «<ULN» из слоя в ячейке таблицы иначе стал бы тегом.
        out.append(html.escape(token, quote=False))

    def gap(index: int) -> str:
        return "" if index == 0 and first_gap_used else model.gaps[index]

    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal" or (tag == "replace" and i2 - i1 == j2 - j1):
            for i, j in zip(range(i1, i2), range(j1, j2), strict=True):
                emit(gap(i), layer.words[j])
        elif tag == "replace":
            emit(gap(i1), _join(layer, j1, j2))
            for i in range(i1 + 1, i2):
                emit(_structure(model.gaps[i]))
        elif tag == "delete":
            for i in range(i1, i2):
                emit(_structure(gap(i)))
        else:
            if not out:
                emit(gap(0))
                first_gap_used = True
            emit(_squash(layer.gaps[j1]) if out else "", _join(layer, j1, j2))
    emit(gap(len(model.words)))
    return "".join(out)


def _join(tokens: _Tokens, start: int, end: int) -> str:
    """Токены подряд; разделители между ними — одиночные пробелы, как в исходном тексте."""
    parts = [tokens.words[start]]
    for index in range(start + 1, end):
        parts.append(_squash(tokens.gaps[index]) + tokens.words[index])
    return "".join(parts)


def _squash(gap: str) -> str:
    return re.sub(r"\s+", " ", gap)


def _structure(gap: str) -> str:
    """Теги и переводы строк разделителя: их нельзя терять, даже если слова ушли."""
    return "".join(_STRUCTURE.findall(gap))


def _is_word(char: str) -> bool:
    return char.isalnum()


def _is_entity(token: str) -> bool:
    return len(token) > 1 and token.startswith("&")


def _phrase(tokens: _Tokens, start: int, end: int) -> str:
    """Короткий пример для текста ошибки: токены через пробел, без тегов."""
    if start == end:
        return "(нет)"
    text = tokens.words[start] + "".join(
        (" " if tokens.gaps[index] else "") + tokens.words[index] for index in range(start + 1, end)
    )
    return text if len(text) <= _EXAMPLE_CHARS else text[: _EXAMPLE_CHARS - 1] + "…"
