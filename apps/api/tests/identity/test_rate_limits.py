"""Лимиты неудачных входов (спека #23, разделы 2–3).

Пороги в тестах уменьшены через настройки. Окна — как в коде: 15 минут для пары
«адрес + IP» и для IP, сутки для адреса.
"""

import asyncio

import httpx
import pytest
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.conftest import MakeUser
from tests.throttling import limited_api

PASSWORD = "correct horse battery staple"
WRONG = "wrong password"
DOCTOR = "doctor@example.com"
IP = "203.0.113.1"
OTHER_IP = "203.0.113.2"
# Caddy во внутренней сети стенда.
CADDY = "172.18.0.2"
STAND_NETWORK = "172.18.0.0/16"


async def login(client: httpx.AsyncClient, email: str, password: str) -> httpx.Response:
    return await client.post("/api/v1/auth/login", json={"email": email, "password": password})


async def login_via(
    client: httpx.AsyncClient, email: str, password: str, forwarded_for: str
) -> httpx.Response:
    return await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
        headers={"X-Forwarded-For": forwarded_for},
    )


def rate_limited_events(logs: list[dict[str, object]]) -> list[dict[str, object]]:
    return [entry for entry in logs if entry["event"] == "http.rate_limited"]


async def fail(client: httpx.AsyncClient, email: str, times: int) -> None:
    for _ in range(times):
        assert (await login(client, email, WRONG)).status_code == 401


async def age_events(maker: async_sessionmaker[AsyncSession], interval: str) -> None:
    async with maker() as session:
        await session.execute(
            text(f"UPDATE identity.throttle_events SET at = at - interval '{interval}'")
        )
        await session.commit()


async def test_pair_limit_blocks_even_the_right_password(
    postgres_url: str, make_user: MakeUser
) -> None:
    await make_user(email=DOCTOR)
    async with limited_api(postgres_url, login_failures_per_pair=3) as client_from:
        client = client_from(IP)
        await fail(client, DOCTOR, 3)

        blocked = await login(client, DOCTOR, PASSWORD)
        other_machine = await login(client_from(OTHER_IP), DOCTOR, PASSWORD)

    assert blocked.status_code == 429
    assert other_machine.status_code == 200


async def test_429_is_a_problem_with_retry_after(postgres_url: str, make_user: MakeUser) -> None:
    await make_user(email=DOCTOR)
    async with limited_api(postgres_url, login_failures_per_pair=2) as client_from:
        client = client_from(IP)
        await fail(client, DOCTOR, 2)

        response = await login(client, DOCTOR, WRONG)

    body = response.json()
    assert response.headers["content-type"] == "application/problem+json"
    assert (body["status"], body["code"]) == (429, "rate_limited")
    assert 0 < body["retry_after"] <= 15 * 60
    assert response.headers["retry-after"] == str(body["retry_after"])


async def test_retry_after_counts_from_the_oldest_extra_failure(
    postgres_url: str, make_user: MakeUser, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    # Неудачи 10 и 5 минут назад и сейчас; при пороге 2 ждать, пока истечёт вторая —
    # 10 минут. От самой старой вышло бы 5 минут, от последней — всё окно.
    await make_user(email=DOCTOR)
    async with limited_api(postgres_url, login_failures_per_pair=3) as client_from:
        client = client_from(IP)
        await fail(client, DOCTOR, 1)
        await age_events(sessionmaker, "5 minutes")
        await fail(client, DOCTOR, 1)
        await age_events(sessionmaker, "5 minutes")
        await fail(client, DOCTOR, 1)

        async with limited_api(postgres_url, login_failures_per_pair=2) as stricter:
            response = await login(stricter(IP), DOCTOR, PASSWORD)

    assert response.status_code == 429
    assert 10 * 60 - 5 <= response.json()["retry_after"] <= 10 * 60


async def test_ip_limit_covers_all_addresses(postgres_url: str, make_user: MakeUser) -> None:
    await make_user(email=DOCTOR)
    async with limited_api(postgres_url, login_failures_per_ip=3) as client_from:
        client = client_from(IP)
        for n in range(3):
            await fail(client, f"leaked{n}@example.com", 1)

        response = await login(client, DOCTOR, PASSWORD)

    assert response.status_code == 429


async def test_account_limit_spans_ips_but_spares_a_known_pair(
    postgres_url: str, make_user: MakeUser
) -> None:
    await make_user(email=DOCTOR)
    async with limited_api(postgres_url, login_failures_per_account=3) as client_from:
        doctor_machine = client_from(IP)
        assert (await login(doctor_machine, DOCTOR, PASSWORD)).status_code == 200
        for n in range(3):
            await fail(client_from(f"198.51.100.{n}"), DOCTOR, 1)

        new_machine = await login(client_from(OTHER_IP), DOCTOR, PASSWORD)
        known_machine = await login(doctor_machine, DOCTOR, PASSWORD)

    assert new_machine.status_code == 429
    assert known_machine.status_code == 200


async def test_unknown_address_is_limited_like_a_real_one(postgres_url: str) -> None:
    async with limited_api(postgres_url, login_failures_per_pair=3) as client_from:
        client = client_from(IP)
        await fail(client, "nobody@example.com", 3)

        response = await login(client, "nobody@example.com", WRONG)

    assert response.status_code == 429


async def test_success_forgets_pair_and_account_failures_but_not_ip(
    postgres_url: str, make_user: MakeUser
) -> None:
    await make_user(email=DOCTOR)
    async with limited_api(
        postgres_url, login_failures_per_pair=3, login_failures_per_ip=4
    ) as client_from:
        client = client_from(IP)
        await fail(client, DOCTOR, 2)
        assert (await login(client, DOCTOR, PASSWORD)).status_code == 200

        # Неудачи пары стёрты: ещё две неудачи — без 429.
        await fail(client, DOCTOR, 2)
        # Неудачи IP остались: 2 + 2 = 4 — порог IP исчерпан.
        response = await login(client, DOCTOR, PASSWORD)

    assert response.status_code == 429


async def test_attempts_are_allowed_again_after_the_window(
    postgres_url: str, make_user: MakeUser, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await make_user(email=DOCTOR)
    async with limited_api(postgres_url, login_failures_per_pair=2) as client_from:
        client = client_from(IP)
        await fail(client, DOCTOR, 2)
        assert (await login(client, DOCTOR, PASSWORD)).status_code == 429

        await age_events(sessionmaker, "16 minutes")
        response = await login(client, DOCTOR, PASSWORD)

    assert response.status_code == 200


async def test_parallel_failures_do_not_exceed_the_pair_limit(
    postgres_url: str, make_user: MakeUser
) -> None:
    await make_user(email=DOCTOR)
    async with limited_api(postgres_url, login_failures_per_pair=3) as client_from:
        client = client_from(IP)
        with structlog.testing.capture_logs() as logs:
            responses = await asyncio.gather(*(login(client, DOCTOR, WRONG) for _ in range(12)))

    statuses = [r.status_code for r in responses]
    assert set(statuses) <= {401, 429}
    assert statuses.count(401) <= 3
    # Пара занята параллельным входом — «login.lock», лимит пары исчерпан — «login.pair».
    limits = {event["limits"] for event in rate_limited_events(logs)}
    assert limits <= {("login.lock",), ("login.pair",)}
    assert len(limits) >= 1


async def test_old_events_are_deleted_when_a_new_one_is_written(
    postgres_url: str, make_user: MakeUser, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await make_user(email=DOCTOR)
    async with limited_api(postgres_url) as client_from:
        client = client_from(IP)
        assert (await login(client, DOCTOR, PASSWORD)).status_code == 200
        await fail(client, DOCTOR, 1)
        await age_events(sessionmaker, "25 hours")

        await fail(client_from(OTHER_IP), DOCTOR, 1)

    async with sessionmaker() as session:
        buckets = (
            await session.scalars(
                text("SELECT bucket FROM identity.throttle_events ORDER BY bucket")
            )
        ).all()
    # Старые неудачи удалены; знакомая пара живёт 30 дней; новая неудача — в трёх ключах.
    assert buckets == ["login.account", "login.ip", "login.known", "login.pair"]


async def test_events_hold_no_address_or_ip_in_clear(
    postgres_url: str, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    async with limited_api(postgres_url) as client_from:
        await fail(client_from(IP), DOCTOR, 1)

    async with sessionmaker() as session:
        rows = (
            await session.scalars(
                text("SELECT row_to_json(e)::text FROM identity.throttle_events e")
            )
        ).all()
    assert len(rows) == 3
    assert not any(DOCTOR in row or IP in row for row in rows)


@pytest.mark.parametrize(
    ("limits", "failures", "expected"),
    [
        ({"login_failures_per_pair": 2}, [(IP, DOCTOR), (IP, DOCTOR)], "login.pair"),
        ({"login_failures_per_ip": 2}, [(IP, "a@example.com"), (IP, "b@example.com")], "login.ip"),
        (
            {"login_failures_per_account": 2},
            [("198.51.100.1", DOCTOR), ("198.51.100.2", DOCTOR)],
            "login.account",
        ),
    ],
)
async def test_rate_limited_log_names_the_limit_but_not_address_or_ip(
    postgres_url: str, limits: dict[str, int], failures: list[tuple[str, str]], expected: str
) -> None:
    # По логу видно, какой лимит сработал: подбор с одной машины, подстановка утечек
    # или подбор с многих IP. Адрес почты и IP в лог не попадают.
    async with limited_api(postgres_url, **limits) as client_from:
        for ip, email in failures:
            await fail(client_from(ip), email, 1)
        with structlog.testing.capture_logs() as logs:
            response = await login(client_from(IP), DOCTOR, PASSWORD)

    assert response.status_code == 429
    [event] = rate_limited_events(logs)
    assert event["limits"] == (expected,)
    assert not any(value in repr(logs) for value in (DOCTOR, IP, *(ip for ip, _ in failures)))


async def test_forwarded_for_from_a_trusted_proxy_is_the_client_address(
    postgres_url: str, make_user: MakeUser
) -> None:
    # За Caddy все запросы приходят с его адреса. Без доверия к его X-Forwarded-For
    # лимиты по IP стали бы общими для всех клиентов стенда.
    await make_user(email=DOCTOR)
    async with limited_api(
        postgres_url, trusted_proxies=(STAND_NETWORK,), login_failures_per_pair=2
    ) as client_from:
        caddy = client_from(CADDY)
        for _ in range(2):
            assert (await login_via(caddy, DOCTOR, WRONG, IP)).status_code == 401

        blocked = await login_via(caddy, DOCTOR, PASSWORD, IP)
        other_client = await login_via(caddy, DOCTOR, PASSWORD, OTHER_IP)

    assert blocked.status_code == 429
    assert other_client.status_code == 200


async def test_forwarded_for_from_an_untrusted_client_is_ignored(
    postgres_url: str, make_user: MakeUser
) -> None:
    # Клиент в обход Caddy подставляет новый адрес в каждый запрос, но лимит пары
    # считается по адресу соединения.
    await make_user(email=DOCTOR)
    async with limited_api(
        postgres_url, trusted_proxies=(STAND_NETWORK,), login_failures_per_pair=2
    ) as client_from:
        attacker = client_from(IP)
        for n in range(2):
            assert (await login_via(attacker, DOCTOR, WRONG, f"198.51.100.{n}")).status_code == 401

        response = await login_via(attacker, DOCTOR, PASSWORD, "198.51.100.9")

    assert response.status_code == 429
