"""Общие зависимости FastAPI."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from clin_compass.core.config import Settings


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """Сессия БД на запрос. Коммит делает обработчик явно; без коммита изменения откатываются."""
    async with request.app.state.sessionmaker() as session:
        yield session


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


DbSession = Annotated[AsyncSession, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]
