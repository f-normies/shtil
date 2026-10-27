"""Пароли (argon2id). Внутренний файл модуля identity.

Хеш занимает десятки миллисекунд CPU, поэтому считается в пуле потоков: цикл событий
в это время продолжает обслуживать запросы и WebSocket. Одновременных хешей не больше
MAX_PARALLEL_HASHES — каждый занимает 64 МиБ памяти.
"""

import asyncio
import threading
from collections.abc import Callable

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

MAX_PARALLEL_HASHES = 4

_hasher = PasswordHasher()
_slots = threading.BoundedSemaphore(MAX_PARALLEL_HASHES)

# Хеш для проверки, когда пользователь не найден: время ответа не выдаёт,
# существует ли email.
_DUMMY_HASH = _hasher.hash("dummy password for timing")


def _limited[T](work: Callable[[], T]) -> T:
    with _slots:
        return work()


def _verify(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


async def hash_password(password: str) -> str:
    return await asyncio.to_thread(_limited, lambda: _hasher.hash(password))


async def verify_password(password_hash: str | None, password: str) -> bool:
    return await asyncio.to_thread(_limited, lambda: _verify(password_hash, password))
