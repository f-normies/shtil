"""argon2 не блокирует цикл событий: вход под нагрузкой не задерживает алерты (метрики 1 и 6)."""

import asyncio
import time

from clin_compass.modules.identity import security


async def test_hashing_keeps_event_loop_responsive() -> None:
    gaps: list[float] = []

    async def heartbeat() -> None:
        last = time.perf_counter()
        while True:
            await asyncio.sleep(0.002)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    beat = asyncio.create_task(heartbeat())
    await asyncio.sleep(0.01)
    started = time.perf_counter()
    await asyncio.gather(*(security.hash_password(f"password {i}") for i in range(6)))
    batch = time.perf_counter() - started
    # Даём heartbeat проснуться и записать последнюю паузу, иначе блокировку не увидим.
    await asyncio.sleep(0.01)
    beat.cancel()

    # Если хеш считается в потоке цикла, gather выполняет все шесть хешей в одной итерации:
    # цикл стоит всю пачку, самая длинная пауза равна её времени. Порог берём от пачки,
    # а не от одного хеша: паузы планировщика (цикл ждёт CPU среди потоков argon2) —
    # это миллисекунды, и от скорости процессора они не зависят. Замер для #47: при
    # блокировке пауза/пачка = 1,00, без неё — не больше 0,03 на 2, 4 и 8 CPU.
    assert max(gaps) < batch / 2


async def test_verify_accepts_right_and_rejects_wrong_password() -> None:
    stored = await security.hash_password("right password")

    assert await security.verify_password(stored, "right password") is True
    assert await security.verify_password(stored, "wrong password") is False
    assert await security.verify_password(None, "any password") is False
