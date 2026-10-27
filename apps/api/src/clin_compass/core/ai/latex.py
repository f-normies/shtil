"""LaTeX в выводе olmOCR 2: формулы и знаки ОХЛП как обычный текст (спека #105).

olmOCR 2 пишет индексы, дроби и знаки в LaTeX («\\( C_{\\text{max}} \\)», «\\geq»). В текстовом
слое PDF те же знаки — обычные символы, дробь — числитель и знаменатель друг под другом, без черты.
Сведение со слоем (`textlayer`) сравнивает слова, поэтому LaTeX снимается до него.
"""

import re

# «C_{max}» — «Cmax».
_LATEX_INDEX = re.compile(r"[_^]\{([^{}]*)\}")
_LATEX_DELIMITER = re.compile(r"\\[(\[][ \t]*|[ \t]*\\[)\]]|\\(?:left|right)(?![a-zA-Z])")
_LATEX_TEXT = re.compile(r"\\(?:text|textit|textbf|mathrm|mathit|mathbf|operatorname)\{([^{}]*)\}")
_LATEX_FRAC = re.compile(r"\\frac\{([^{}]*)\}\{([^{}]*)\}")
_LATEX_SPACE = re.compile(r"\\(?:[,;:! ]|q?quad(?![a-zA-Z]))")
_LATEX_SYMBOLS = {
    "times": "×",
    "cdot": "·",
    "geq": "≥",
    "ge": "≥",
    "leq": "≤",
    "le": "≤",
    "neq": "≠",
    "pm": "±",
    "approx": "≈",
    "sim": "~",
    "to": "→",
    "rightarrow": "→",
    "infty": "∞",
    "circ": "°",
    "alpha": "α",
    "beta": "β",
    "gamma": "γ",
    "delta": "δ",
    "kappa": "κ",
    "lambda": "λ",
    "mu": "μ",
    "%": "%",
}
_LATEX_SYMBOL = re.compile(
    r"\\(%|(?:" + "|".join(sorted(_LATEX_SYMBOLS, key=len, reverse=True)) + r")(?![a-zA-Z]))"
)


def strip_latex(text: str) -> str:
    """Текст модели без LaTeX: индексы, `\\text{}`, дроби, знаки и скобки формул."""
    previous = None
    while previous != text:
        previous = text
        text = _LATEX_TEXT.sub(r"\1", text)
        text = _LATEX_FRAC.sub(r"\1 \2", text)
    text = _LATEX_INDEX.sub(r"\1", text)
    text = _LATEX_SYMBOL.sub(lambda m: _LATEX_SYMBOLS[m.group(1)], text)
    text = _LATEX_SPACE.sub(" ", text)
    return _LATEX_DELIMITER.sub("", text)
