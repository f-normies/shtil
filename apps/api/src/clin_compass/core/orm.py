"""Базовый класс моделей SQLAlchemy. Каждый модуль держит таблицы в своей схеме Postgres."""

from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, MetaData, func, text
from sqlalchemy.orm import DeclarativeBase, MappedColumn, mapped_column

# Предсказуемые имена ограничений: Alembic сможет их удалять и переименовывать.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def uuid_pk() -> MappedColumn[UUID]:
    """Первичный ключ UUIDv7: генерирует Postgres 18 (`uuidv7()`), ключи сортируются по времени."""
    return mapped_column(primary_key=True, server_default=text("uuidv7()"))


def created_at() -> MappedColumn[Any]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


def timestamp(*, nullable: bool = False) -> MappedColumn[Any]:
    return mapped_column(DateTime(timezone=True), nullable=nullable)


__all__ = ["Base", "created_at", "timestamp", "uuid_pk"]
