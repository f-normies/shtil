"""Лимиты частоты входа и приглашений (спека #23): события в окне по ключу.

Ключ хранится как HMAC-SHA256 с секретом из настроек (`digest`): в таблице нет адресов почты
и IP, а без секрета хеш не перебрать. Функции ниже принимают уже готовый `digest`.
Окно скользящее: считаются события за последние `window`. Время берётся в БД.
"""

import hashlib
import hmac
import ipaddress
import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import ColumnElement, delete, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from clin_compass.modules.identity.models import ThrottleEvent

UNKNOWN_IP = "unknown"
_IPV6_PREFIX = 64
# Дольше ни одно окно не смотрит. Знакомые пары «адрес + IP» живут 30 дней.
_RETENTION = timedelta(days=1)
KNOWN_BUCKET = "login.known"
KNOWN_WINDOW = timedelta(days=30)


@dataclass(frozen=True)
class Limit:
    """Не больше `limit` событий вида `bucket` по одному ключу за `window`."""

    bucket: str
    limit: int
    window: timedelta


def ip_key(host: str | None) -> str:
    """IPv4 — адрес целиком, IPv6 — его сеть /64. Не адрес — `unknown`."""
    try:
        address = ipaddress.ip_address(host or "")
    except ValueError:
        return UNKNOWN_IP
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return str(address.ipv4_mapped)
        return str(ipaddress.IPv6Network((address, _IPV6_PREFIX), strict=False))
    return str(address)


def digest(secret: bytes, key: str) -> str:
    """HMAC-SHA256 ключа лимита в hex: так ключ хранится в таблице и в имени блокировки."""
    return hmac.new(secret, key.encode(), hashlib.sha256).hexdigest()


def seconds(value: timedelta) -> int:
    """Секунды до повтора: вверх и не меньше одной."""
    return max(1, math.ceil(value.total_seconds()))


async def retry_after(session: AsyncSession, limit: Limit, key_digest: str) -> int | None:
    """Секунды до следующей разрешённой попытки; `None` — лимит не исчерпан.

    Попытка станет разрешённой, когда в окне останется `limit - 1` событий: истечёт
    самое старое из лишних.
    """
    now = await _now(session)
    times = (
        await session.scalars(
            select(ThrottleEvent.at)
            .where(*_same(limit.bucket, key_digest), ThrottleEvent.at > now - limit.window)
            .order_by(ThrottleEvent.at)
        )
    ).all()
    if len(times) < limit.limit:
        return None
    return seconds(times[len(times) - limit.limit] + limit.window - now)


async def seen(session: AsyncSession, bucket: str, key_digest: str, window: timedelta) -> bool:
    """Было ли событие `bucket` по ключу за последние `window`."""
    now = await _now(session)
    found = await session.scalar(
        select(ThrottleEvent.id)
        .where(*_same(bucket, key_digest), ThrottleEvent.at > now - window)
        .limit(1)
    )
    return found is not None


async def record(session: AsyncSession, bucket: str, key_digest: str) -> None:
    """Записать событие и удалить события, которые не смотрит ни одно окно."""
    now = await _now(session)
    await session.execute(
        delete(ThrottleEvent).where(
            or_(
                (ThrottleEvent.bucket == KNOWN_BUCKET) & (ThrottleEvent.at < now - KNOWN_WINDOW),
                (ThrottleEvent.bucket != KNOWN_BUCKET) & (ThrottleEvent.at < now - _RETENTION),
            )
        )
    )
    session.add(ThrottleEvent(bucket=bucket, key_hash=key_digest))
    await session.flush()


async def forget(session: AsyncSession, bucket: str, key_digest: str) -> None:
    """Удалить события `bucket` по ключу."""
    await session.execute(delete(ThrottleEvent).where(*_same(bucket, key_digest)))


async def try_lock(session: AsyncSession, key_digest: str) -> bool:
    """Транзакционная advisory-блокировка без ожидания: `False`, если ключ уже занят."""
    locked = await session.scalar(
        text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": key_digest},
    )
    return bool(locked)


def _same(bucket: str, key_digest: str) -> tuple[ColumnElement[bool], ...]:
    return (ThrottleEvent.bucket == bucket, ThrottleEvent.key_hash == key_digest)


async def _now(session: AsyncSession) -> datetime:
    now: datetime = await session.scalar(select(func.now()))  # type: ignore[assignment]
    return now
