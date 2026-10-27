"""Врачи нагрузочного теста (#41). Запускается внутри контейнера api копии стенда:

    docker compose exec -T -e LOADTEST_PASSWORD="$LOADTEST_PASSWORD" api \
        python - < loadtest/doctors.py

API создания врача нет, поэтому сервис identity вызывается напрямую. Повторный запуск
пропускает существующих. Список совпадает с accounts.py: 50 врачей и врач замера.
"""

import asyncio
import os

from clin_compass.core.config import Settings
from clin_compass.core.db import make_engine, make_sessionmaker
from clin_compass.modules.identity import service as identity
from clin_compass.modules.identity.schemas import Role

DOMAIN = "loadtest.shtil.ru"
DOCTORS = [
    (f"lt-doctor-{n:02d}@{DOMAIN}", f"Нагрузочный Врач {n:02d}") for n in range(1, 51)
]
DOCTORS.append((f"lt-probe-doctor@{DOMAIN}", "Врач Замер Алертов"))


async def main() -> None:
    password = os.environ["LOADTEST_PASSWORD"]
    engine = make_engine(Settings().database_url)
    created = 0
    async with make_sessionmaker(engine)() as session:
        for email, name in DOCTORS:
            if await identity.get_user_by_email(session, email) is None:
                await identity.create_user(
                    session,
                    email=email,
                    password=password,
                    role=Role.DOCTOR,
                    full_name=name,
                )
                created += 1
        await session.commit()
    await engine.dispose()
    print(f"врачей создано: {created}")


asyncio.run(main())
