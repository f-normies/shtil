"""Таблицы модуля knowledge (схема Postgres `knowledge`, спека #27, раздел 4).

Старые версии документов не удаляются: на их фрагменты ссылаются подтверждённые
рекомендации #28. Новая версия получает `active`, прежняя — `superseded`.
"""

from datetime import datetime
from uuid import UUID

from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import (
    CheckConstraint,
    Computed,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from clin_compass.core.orm import Base, created_at, timestamp, uuid_pk

SCHEMA = "knowledge"
# Размерность Giga-Embeddings. Тип `vector` pgvector индексирует только до 2000 измерений.
EMBEDDING_DIMENSIONS = 2048


class Document(Base):
    """Версия документа из манифеста: клиническая рекомендация или инструкция препарата."""

    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("kind IN ('clinical_guideline', 'drug_label')", name="kind"),
        CheckConstraint("status IN ('active', 'superseded')", name="status"),
        CheckConstraint(
            "(status = 'superseded') = (superseded_at IS NOT NULL)", name="superseded_complete"
        ),
        # Один действующий документ на источник манифеста.
        Index(
            "uq_documents_active_source_id",
            "source_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = uuid_pk()
    # `id` источника в манифесте knowledge/.
    source_id: Mapped[str] = mapped_column(Text)
    version: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    # SHA-256 исходного JSON или PDF: тот же хеш — переиндексировать нечего.
    content_hash: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()
    superseded_at: Mapped[datetime | None] = timestamp(nullable=True)


class Fragment(Base):
    """Фрагмент документа: единица поиска и цитирования."""

    __tablename__ = "fragments"
    __table_args__ = (
        CheckConstraint("kind IN ('recommendation', 'text', 'table')", name="kind"),
        # УУР и УДД есть только у пункта рекомендации.
        CheckConstraint("kind = 'recommendation' OR (uur IS NULL AND udd IS NULL)", name="levels"),
        UniqueConstraint("document_id", "key"),
        Index(
            "ix_knowledge_fragments_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "halfvec_cosine_ops"},
        ),
        Index("ix_knowledge_fragments_tsv", "tsv", postgresql_using="gin"),
        {"schema": SCHEMA},
    )

    id: Mapped[UUID] = uuid_pk()
    document_id: Mapped[UUID] = mapped_column(ForeignKey(f"{SCHEMA}.documents.id"))
    # Порядок фрагмента в документе, с 1: по нему `read_section` отдаёт раздел.
    position: Mapped[int] = mapped_column(Integer)
    # `{source_id}/{раздел}/{номер в разделе}`: по нему набор eval ссылается на фрагмент.
    key: Mapped[str] = mapped_column(Text)
    # `doc_3` у рекомендаций или `4.3` у ОХЛП и подпись раздела.
    section: Mapped[str] = mapped_column(Text)
    section_title: Mapped[str] = mapped_column(Text)
    # Заголовки от раздела до фрагмента через « > ».
    heading_path: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    # С этим текстом сверяется цитата ответа.
    text: Mapped[str] = mapped_column(Text)
    uur: Mapped[str | None] = mapped_column(Text)
    udd: Mapped[int | None] = mapped_column(SmallInteger)
    # Векторы и tsvector читает только поиск в SQL: в объекты они не загружаются.
    embedding: Mapped[list[float]] = mapped_column(HALFVEC(EMBEDDING_DIMENSIONS), deferred=True)
    # Выражение записано так, как его возвращает Postgres: иначе `alembic check` предупреждает,
    # что вычисляемая колонка изменилась.
    tsv: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('russian'::regconfig, heading_path || ' '::text || text)", persisted=True
        ),
        deferred=True,
    )


__all__ = ["EMBEDDING_DIMENSIONS", "SCHEMA", "Document", "Fragment"]
