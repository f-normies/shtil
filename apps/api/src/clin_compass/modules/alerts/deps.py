"""Публичные зависимости модуля alerts: книга правил для модулей, которые вызывают check_results."""

from typing import Annotated

from fastapi import Depends, Request

from clin_compass.modules.alerts.rules import RuleBook, repository_knowledge_dir

# RuleBook реэкспортируется: вызывающим check_results нужен тип книги правил. Путь к knowledge/
# по умолчанию нужен `make seed` (#19), чтобы читать правила как при старте api. Сам модуль
# alerts.rules закрыт контрактом import-linter.


def get_rules(request: Request) -> RuleBook:
    """Книга правил, прочитанная при старте api (`main.create_app`)."""
    rules: RuleBook = request.app.state.alert_rules
    return rules


AlertRules = Annotated[RuleBook, Depends(get_rules)]

__all__ = ["AlertRules", "RuleBook", "get_rules", "repository_knowledge_dir"]
