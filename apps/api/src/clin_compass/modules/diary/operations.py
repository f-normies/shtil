"""Канонические хеши и квитанции идемпотентных команд."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from clin_compass.modules.diary.errors import DiaryError
from clin_compass.modules.diary.models import Operation


def canonical_command_hash(
    command_type: str,
    body: BaseModel,
    *,
    audio: BaseModel | None = None,
    include_defaults: bool = False,
) -> str:
    """Хешировать нормализованную команду, включая тип, версию и метаданные аудио."""
    payload = body.model_dump(
        mode="json", exclude_unset=not include_defaults, exclude={"operation_id"}
    )
    if isinstance(payload.get("occurred_at"), str):
        occurred_at = datetime.fromisoformat(payload["occurred_at"].replace("Z", "+00:00"))
        payload["occurred_at"] = occurred_at.astimezone(UTC).isoformat()
    if isinstance(payload.get("mood_words"), list):
        payload["mood_words"] = sorted(payload["mood_words"])
    normalized: dict[str, Any] = {"command_type": command_type, "body": payload}
    if audio is not None:
        audio_payload = audio.model_dump(mode="json", exclude={"storage_key"})
        normalized["audio"] = audio_payload
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


async def lock_operation_key(session: AsyncSession, patient_id: UUID, operation_id: UUID) -> None:
    """Serialize concurrent retries of one patient operation before checking its receipt."""
    key = f"diary.operation:{patient_id}:{operation_id}"
    await session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(key, 0))))


async def replay_receipt(
    session: AsyncSession,
    *,
    patient_id: UUID,
    operation_id: UUID,
    operation_type: str,
    request_hash: str,
) -> dict[str, Any] | None:
    """Вернуть прежнюю квитанцию или отклонить повтор с изменённым телом."""
    row = await session.scalar(
        select(Operation).where(
            Operation.patient_id == patient_id,
            Operation.operation_id == operation_id,
        )
    )
    if row is None:
        return None
    if row.operation_type != operation_type or row.request_hash != request_hash:
        raise DiaryError(409, "operation_conflict", "Operation key was used for other data")
    return row.receipt


def save_receipt(
    session: AsyncSession,
    *,
    patient_id: UUID,
    operation_id: UUID,
    operation_type: str,
    request_hash: str,
    receipt: BaseModel,
) -> None:
    """Добавить неизменяемую квитанцию в текущую транзакцию."""
    session.add(
        Operation(
            patient_id=patient_id,
            operation_id=operation_id,
            operation_type=operation_type,
            request_hash=request_hash,
            receipt=receipt.model_dump(mode="json"),
        )
    )
