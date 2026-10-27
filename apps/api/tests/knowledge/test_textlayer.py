"""Сведение вывода olmOCR 2 с текстовым слоем PDF (спека #27, 3.4). Тексты синтетические.

Слова берутся из текстового слоя, от модели — только разметка: строки, заголовки, HTML-таблицы.
"""

from dataclasses import dataclass

import pytest

from clin_compass.core.ai.textlayer import (
    MAX_UNMATCHED,
    PageMismatch,
    ReconciledPage,
    reconcile_pages,
)

# Так текстовый слой отдаёт pypdfium2: строки по ширине страницы, номер страницы отдельной строкой.
LAYER = """3
4.5. Взаимодействие с другими лекарственными препаратами
Ингибиторы CYP1A2 (например, флувоксамин, ципрофлоксацин) повышают максимальную
концентрацию (Cmax) маритупирдина. Внутрь по 1 таблетке 2 раза в сутки. Нежелательные
реакции: редко (≥1/10000, но <1/1000), при необходимости - до 6 недель.
Таблица 1
Часто головокружение1, сонливость1
Нечасто снижение аппетита
Головокружение наблюдалось в начале лечения и проходило самостоятельно.
"""

# Так ту же страницу пишет olmOCR 2: markdown, абзацы одной строкой, таблица в HTML.
MODEL = """## 4.5. Взаимодействие с другими лекарственными препаратами

Ингибиторы CYP1A2 (например, флувоксамин, ципрофлоксацин) повышают максимальную концентрацию (Cmax) маритупирдина. Внутрь по 1 таблетке 2 раза в сутки. Нежелательные реакции: редко (≥1/10000, но <1/1000), при необходимости - до 6 недель.

**Таблица 1**

<table>
  <tr>
    <td>Часто</td>
    <td>головокружение1, сонливость1</td>
  </tr>
  <tr>
    <td>Нечасто</td>
    <td>снижение аппетита</td>
  </tr>
</table>

Головокружение наблюдалось в начале лечения и проходило самостоятельно.
"""  # noqa: E501
# Сведённый текст — HTML-разметка: знаки слоя «<», «>» и «&» в нём экранированы.
RECONCILED = MODEL.replace("<1/1000", "&lt;1/1000")
PARAGRAPH = MODEL.split("\n\n")[1]
TABLE = MODEL[MODEL.index("<table>") : MODEL.index("</table>") + len("</table>")]
LAST = "Головокружение наблюдалось в начале лечения и проходило самостоятельно."


@dataclass(frozen=True)
class _Page:
    number: int
    text: str
    layer_text: str


def _one(model: str, layer: str) -> tuple[ReconciledPage, list[PageMismatch]]:
    [page], mismatches = reconcile_pages([_Page(3, model, layer)])
    return page, mismatches


@pytest.mark.parametrize(
    "layer",
    [
        pytest.param(LAYER, id="номер страницы сверху"),
        pytest.param(LAYER.removeprefix("3\n") + "\n3\n", id="номер страницы снизу"),
        pytest.param(LAYER.replace(" ", "   ").replace("\n", "\r\n"), id="пробелы и переводы"),
        pytest.param(LAYER.replace("3\n", "3 из 12\n", 1), id="колонтитул «3 из 12»"),
    ],
)
def test_page_that_matches_keeps_model_markup(layer: str) -> None:
    page, mismatches = _one(MODEL, layer)

    assert mismatches == []
    assert page == ReconciledPage(3, RECONCILED)


@pytest.mark.parametrize(
    ("model", "layer", "expected", "absent"),
    [
        pytest.param(
            MODEL.replace("флувоксамин", "флуоксамин"),
            LAYER,
            "(например, флувоксамин, ципрофлоксацин)",
            "флуоксамин",
            id="модель поправила слово",
        ),
        pytest.param(
            MODEL.replace("(Cmax)", "(C_{max})"),
            LAYER.replace("(Cmax)", "(Сmax)"),  # в слое кириллическая «С»
            "максимальную концентрацию (Сmax) маритупирдина",
            "{",
            id="LaTeX-индекс",
        ),
        pytest.param(
            MODEL.replace(
                "головокружение1, сонливость1", "головокружение¹, сонливость<sup>1</sup>"
            ).replace("\nГоловокружение", "\n¹ Головокружение"),
            LAYER.replace("\nГоловокружение", "\n1\nГоловокружение"),
            "<td>головокружение1, сонливость1</td>",
            "¹",
            id="надстрочная сноска",
        ),
        pytest.param(
            MODEL.replace("необходимости - до", "необходимости – до"),
            LAYER.replace("необходимости - до", "необходимости − до"),
            "при необходимости − до 6 недель",
            "–",
            id="разные тире",
        ),
        pytest.param(
            MODEL,
            LAYER.replace("(≥1/10000", "(≥l/10000"),
            "редко (≥l/10000, но &lt;1/1000)",
            "≥1/10000",
            id="опечатка слоя остаётся",
        ),
        pytest.param(
            MODEL.replace("в сутки.", "в сутки утром."),
            LAYER,
            "2 раза в сутки. Нежелательные",
            "утром",
            id="лишнее слово модели",
        ),
        pytest.param(
            MODEL.replace("по 1 таблетке", "по таблетке"),
            LAYER,
            "Внутрь по 1 таблетке 2 раза",
            "по таблетке",
            id="пропущенное моделью слово",
        ),
        pytest.param(
            MODEL.replace("снижение аппетита", "снижение апетита"),
            LAYER,
            "<tr>\n    <td>Нечасто</td>\n    <td>снижение аппетита</td>\n  </tr>",
            "апетита",
            id="слово в ячейке таблицы",
        ),
    ],
)
def test_words_come_from_text_layer(model: str, layer: str, expected: str, absent: str) -> None:
    page, mismatches = _one(model, layer)

    assert mismatches == []
    assert expected in page.text
    assert absent not in page.text
    assert page.text.startswith("## 4.5. Взаимодействие")
    assert "<table>\n  <tr>\n    <td>Часто</td>" in page.text


@pytest.mark.parametrize(
    ("model", "layer", "expected"),
    [
        pytest.param(
            "<table><tr><td>АЛТ &lt;ULN</td><td>нет коррекции</td></tr></table>",
            "АЛТ <ULN нет коррекции",
            "<table><tr><td>АЛТ &lt;ULN</td><td>нет коррекции</td></tr></table>",
            id="«<» в ячейке",
        ),
        pytest.param(
            "<table><tr><td>СКФ <60</td><td>АЛТ &gt;3</td><td>A&B</td></tr></table>",
            "СКФ <60 АЛТ >3 A&B",
            "<table><tr><td>СКФ &lt;60</td><td>АЛТ &gt;3</td><td>A&amp;B</td></tr></table>",
            id="«<60», «>» и «&» в ячейках",
        ),
        pytest.param(
            "СКФ <60 мл/мин; ЧСС >100; R&D; АЛТ <ULN.",
            "СКФ <60 мл/мин; ЧСС >100; R&D; АЛТ <ULN.",
            "СКФ &lt;60 мл/мин; ЧСС &gt;100; R&amp;D; АЛТ &lt;ULN.",
            id="в обычном тексте",
        ),
        pytest.param(
            "Синтетин &amp;lt; вода.",
            "Синтетин &lt; вода.",
            "Синтетин &amp;lt; вода.",
            id="сущность в слое",
        ),
    ],
)
def test_layer_html_characters_are_escaped(model: str, layer: str, expected: str) -> None:
    page, mismatches = _one(model, layer)

    assert mismatches == []
    assert page.text == expected


@pytest.mark.parametrize(
    "model",
    [
        pytest.param(MODEL.replace(PARAGRAPH, ""), id="пропущен абзац"),
        pytest.param(f"{LAST}\n\n" + MODEL.replace(LAST, ""), id="абзац переставлен"),
        pytest.param("", id="пустой текст модели"),
    ],
)
def test_diverged_page_is_mismatch(model: str) -> None:
    pages = [_Page(1, MODEL, LAYER), _Page(2, model, LAYER)]

    reconciled, [mismatch] = reconcile_pages(pages)

    assert mismatch.number == 2
    assert mismatch.unmatched > MAX_UNMATCHED
    assert 0 < len(mismatch.examples) <= 5
    assert all(" → " in example for example in mismatch.examples)
    assert not any("td>" in example for example in mismatch.examples)
    assert reconciled[0] == ReconciledPage(1, RECONCILED)


def test_latex_indices_are_markup_not_words() -> None:
    # olmOCR 2 пишет индексы в LaTeX; без этого каждый «C_{max}» давал бы 5 несведённых токенов.
    model = MODEL + "Показатели: " + ", ".join(["C_{max}", "T_{1/2}", "AUC_{0-t}"] * 8) + ".\n"
    layer = LAYER + "Показатели: " + ", ".join(["Сmax", "Т1/2", "AUC0-t"] * 8) + ".\n"

    page, mismatches = _one(model, layer)

    assert mismatches == []
    assert "Показатели: Сmax, Т1/2, AUC0-t, Сmax," in page.text


def test_wrong_word_on_short_page_is_not_mismatch() -> None:
    layer = (
        "4.3. Противопоказания\nГиперчувствительность к синтетину или вспомогательным веществам."
        " Беременность и период грудного вскармливания. Тяжелая почечная недостаточность."
        " Детский возраст до 18 лет. Совместный прием с ингибиторами моноаминоксидазы."
    )

    page, mismatches = _one(layer.replace("синтетину", "синтетиму"), layer)

    # Одно слово — 2 несведённых токена из 68, это 2,9 %: доля без нижней границы сработала бы.
    assert mismatches == []
    assert page.text == layer


def test_missing_sentence_on_long_page_is_mismatch() -> None:
    filler = " ".join(f"Предложение номер {i} о синтетине без изменений." for i in range(60))
    missing = "Этот абзац модель пропустила целиком, и сведение должно это заметить."
    layer = f"{LAYER}{filler}\n{missing}\n{filler}\n"
    model = f"{MODEL}\n{filler}\n\n{filler}\n"

    _, [mismatch] = _one(model, layer)

    # Доля мала: страница длинная. Пропуск выдаёт длинный участок несведённых токенов подряд.
    assert mismatch.unmatched < MAX_UNMATCHED
    assert mismatch.examples == (
        "(нет) → Этот абзац модель пропустила целиком, и сведение должно это…",
    )


def test_mismatch_examples_show_model_and_layer_words() -> None:
    model = MODEL.replace(PARAGRAPH, "Совсем другой абзац про синтетин.")

    _, [mismatch] = _one(model, LAYER)

    assert any(e.startswith("Совсем другой") and "Ингибиторы" in e for e in mismatch.examples)


@pytest.mark.parametrize(
    "layer",
    [
        pytest.param("", id="пусто"),
        pytest.param(" \r\n\n", id="только пробелы"),
        pytest.param("7\n", id="только номер страницы"),
    ],
)
def test_page_without_text_layer_is_mismatch(layer: str) -> None:
    # Скан: сводить не с чем. Сканы — задача #30, без слоя документ не индексируется.
    pages = [_Page(1, MODEL, LAYER), _Page(2, "Скан: текст модели 123.", layer)]

    reconciled, mismatches = reconcile_pages(pages)

    assert mismatches == [PageMismatch(2, 1.0, (), no_layer=True)]
    assert reconciled[0] == ReconciledPage(1, RECONCILED)


def test_short_text_layer_is_reconciled() -> None:
    page, mismatches = _one(
        "Рисунок 7. Схема лечения синтетином", "Рисунок 1. Схема лечения синтетином"
    )

    assert mismatches == []
    assert page == ReconciledPage(3, "Рисунок 1. Схема лечения синтетином")
