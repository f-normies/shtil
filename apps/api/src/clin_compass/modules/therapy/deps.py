"""Публичные зависимости модуля therapy: справочник препаратов, прочитанный при старте api."""

from typing import Annotated

from fastapi import Depends, Request

from clin_compass.modules.therapy.checks import Catalog

# Catalog реэкспортируется: сиду и CLI нужен справочник для сверки препаратов с ГРЛС.
# Сам модуль therapy.checks закрыт контрактом import-linter.
__all__ = ["Catalog", "DrugCatalog", "get_catalog"]


def get_catalog(request: Request) -> Catalog:
    catalog: Catalog = request.app.state.drug_catalog
    return catalog


DrugCatalog = Annotated[Catalog, Depends(get_catalog)]
