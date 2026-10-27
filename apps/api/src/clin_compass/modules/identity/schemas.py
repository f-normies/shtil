"""Публичные схемы модуля identity."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr


class Role(StrEnum):
    DOCTOR = "doctor"
    PATIENT = "patient"
    ADMIN = "admin"


class DisabledReason(StrEnum):
    """Почему вход пользователя закрыт. Значение уходит клиенту как `code` ответа 403."""

    CONSENT_REVOKED = "consent_revoked"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: UUID
    email: str
    role: Role
    full_name: str


class ActiveSession(BaseModel):
    """Действующая сессия: кому принадлежит, ключ (хеш токена) и до какого момента действует."""

    model_config = ConfigDict(frozen=True)

    user: UserOut
    key: str
    expires_at: datetime


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
