"""Ключи лимитов (спека #23, раздел 2): IP — IPv4 целиком, IPv6 — сеть /64; хеш — HMAC."""

import hashlib

import pytest

from clin_compass.modules.identity.throttle import digest, ip_key


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("203.0.113.7", "203.0.113.7"),
        # Внутри своей /64 клиент меняет адрес свободно: вся сеть — один ключ.
        ("2001:db8:1:2:aaaa::1", "2001:db8:1:2::/64"),
        ("2001:db8:1:2:ffff:ffff:ffff:ffff", "2001:db8:1:2::/64"),
        ("::ffff:203.0.113.7", "203.0.113.7"),
        (None, "unknown"),
        ("testclient", "unknown"),
    ],
)
def test_ip_key(host: str | None, expected: str) -> None:
    assert ip_key(host) == expected


def test_digest_depends_on_the_secret() -> None:
    # Без секрета SHA-256 от IPv4 перебирается за минуты: 2^32 вариантов.
    key = "203.0.113.7"

    assert digest(b"one", key) != digest(b"two", key)
    assert digest(b"one", key) != hashlib.sha256(key.encode()).hexdigest()
    assert len(digest(b"one", key)) == 64
