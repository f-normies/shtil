"""Перекрёстные проверки `patient-help.yaml` (спека #57, раздел 4).

Отдельно от `checks.py`: тот уже длиннее 400 строк. Общие помощники берутся оттуда.
"""

import datetime as dt
from collections.abc import Iterator
from typing import TYPE_CHECKING

from clin_compass_knowledge.checks import _check_primary, _check_refs, _check_unique
from clin_compass_knowledge.issues import Issue

if TYPE_CHECKING:
    from clin_compass_knowledge.loader import Knowledge

FILE = "patient-help.yaml"


def check_patient_help(kn: "Knowledge") -> Iterator[Issue]:
    """Ошибки файла экрана помощи; файла нет — проверять нечего."""
    data = kn.patient_help
    if data is None:
        return
    yield from _check_refs(kn, FILE, data)
    if data.checked_on > dt.date.today():
        msg = f"дата проверки {data.checked_on} ещё не наступила"
        yield Issue(FILE, "checked_on", msg)
    yield from _check_primary(kn, FILE, "sources", data.sources)
    ids = ((f"contacts[{i}].id", c.id) for i, c in enumerate(data.contacts))
    yield from _check_unique(FILE, ids)
    primary = sum(c.primary for c in data.contacts)
    if primary != 1:
        msg = f"нужен ровно один контакт с primary: true, найдено {primary}"
        yield Issue(FILE, "contacts", msg)
    for i, contact in enumerate(data.contacts):
        yield from _check_primary(kn, FILE, f"contacts[{i}].sources", contact.sources)
