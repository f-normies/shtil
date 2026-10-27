"""Непрозрачные токены (сессии, приглашения). В БД хранится только SHA-256 токена."""

import hashlib
import secrets


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
